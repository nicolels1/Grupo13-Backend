from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from src.entities.fidelidade import RegraCriar
from src.models.fidelidade import MovimentoPontos, RegraFidelidade
from src.models.vendas import Pedido
from src.repositories import cliente_sem_conta_repository, fidelidade_repository, usuario_repository
from src.use_cases import fidelidade
from src.use_cases.erros import Conflito, RegraDeNegocio
from tests.apoio import ID_FUNCIONARIO, SessaoFalsa, api, funcionario  # noqa: F401 (api é fixture)
from tests.apoio_vendas import ID_CLIENTE


def sql(consulta):
    return str(consulta.compile(dialect=postgresql.dialect()))


# o passivo soma o que sobra em cada lote pelo valor do ponto da época (K3), separado por marca (P10)
def test_passivo_por_marca_pelo_valor_da_epoca():
    consulta = sql(fidelidade_repository.consulta_passivo())

    assert "sum(lote_pontos.restante * lote_pontos.valor_ponto)" in consulta
    assert "WHERE lote_pontos.restante > " in consulta
    assert "GROUP BY lote_pontos.marca" in consulta


# ---------- regras (P10) ----------

def regra(id_regra, marca, valida_desde, **campos):
    return RegraFidelidade(
        id_regra=id_regra, marca=marca, valida_desde=valida_desde, valor_ponto=Decimal("0.05"),
        pontos_por_real=Decimal("1"), multiplicador_online=Decimal("2"), bonus_ouro=Decimal("0.5"), validade_meses=12,
        validade_meses_ouro=18, limite_prata=2000, limite_ouro=5000, resgate_minimo=100, id_criado_por=None,
        criado_em=datetime(2026, 10, 1, tzinfo=timezone.utc), **campos,
    )


NOVA = {"marca": "lorenzi", "valida_desde": "2026-11-01", "valor_ponto": "0.04", "pontos_por_real": "1",
        "multiplicador_online": "2", "bonus_ouro": "0.5", "validade_meses": 12, "validade_meses_ouro": 18,
        "limite_prata": 2000, "limite_ouro": 5000, "resgate_minimo": 100}


@pytest.fixture
def regras(monkeypatch):
    guardadas = [regra(4, "lorenzi", date(2026, 10, 1)), regra(3, "vulto", date(2026, 10, 1)),
                 regra(2, "vulto", date(2025, 3, 1)), regra(1, "vulto", date(2025, 1, 1))]
    monkeypatch.setattr(fidelidade, "hoje", lambda: date(2026, 10, 20))

    def listar(db, marca=None):
        ordem = sorted(guardadas, key=lambda r: (r.marca, -r.valida_desde.toordinal(), -r.id_regra))
        return [r for r in ordem if marca in (None, r.marca)]

    monkeypatch.setattr(fidelidade_repository, "listar_regras", listar)
    return guardadas


def test_vigente_e_a_mais_nova_ate_hoje_de_cada_marca(regras):
    regras.append(regra(5, "vulto", date(2026, 12, 1)))  # futura: ainda não vale

    vigentes = {(r["marca"], r["id_regra"]) for r in fidelidade.listar_regras(SessaoFalsa()) if r["vigente"]}

    assert vigentes == {("lorenzi", 4), ("vulto", 3)}


def test_regra_nova_vale_dali_em_diante_e_guarda_quem_criou(regras):
    db = SessaoFalsa(primeiro_id=6)
    dados = RegraCriar(**NOVA).model_dump()
    db.refresh = lambda r: (setattr(r, "id_regra", 6), setattr(r, "criado_em", datetime.now(timezone.utc)),
                            regras.append(r))

    criada = fidelidade.criar_regra(db, ID_FUNCIONARIO, dados)

    assert (criada["id_regra"], criada["id_criado_por"], criada["vigente"], db.commits) == (6, ID_FUNCIONARIO, False, 1)


@pytest.mark.parametrize("dia", ["2026-10-20", "2026-09-01"])
def test_regra_nova_nunca_vale_para_hoje_nem_para_tras(regras, dia):
    with pytest.raises(RegraDeNegocio, match="amanhã"):
        fidelidade.criar_regra(SessaoFalsa(), ID_FUNCIONARIO, RegraCriar(**{**NOVA, "valida_desde": dia}).model_dump())


@pytest.mark.parametrize("campos, erro", [
    ({"validade_meses_ouro": None}, "validade das duas"),
    ({"validade_meses_ouro": 6}, "validade no Ouro"),
    ({"limite_prata": 5000}, "limite do Ouro"),
])
def test_regra_incoerente_e_recusada(campos, erro):
    with pytest.raises(ValidationError, match=erro):
        RegraCriar(**{**NOVA, **campos})


def test_regras_exigem_gerenciar_fidelidade(api, regras):
    assert api(SessaoFalsa(), usuario=funcionario(), permitido=False).get("/fidelidade/regras").status_code == 403
    resposta = api(SessaoFalsa(), usuario=funcionario()).get("/fidelidade/regras?marca=vulto")
    assert resposta.status_code == 200 and [r["id_regra"] for r in resposta.json()["items"]] == [3, 2, 1]


# ---------- resgate ----------

@pytest.fixture
def pontos(monkeypatch, regras):
    """Cliente com 1.000 pontos: 400 de antes de mar/2025 (R$ 0,10) e 600 depois (R$ 0,05)."""
    lotes = [(400, Decimal("0.10")), (600, Decimal("0.05"))]

    def valor(pontos_usados):
        total, falta = Decimal("0"), pontos_usados
        for restante, valor_ponto in lotes:  # FIFO, como o trigger
            usado = min(restante, falta)
            total, falta = total + usado * valor_ponto, falta - usado
        return total

    estado = SimpleNamespace(saldo=1000, gasto=None)
    monkeypatch.setattr(fidelidade_repository, "regra_vigente", lambda db, marca, hoje: regra(9, marca, hoje))
    monkeypatch.setattr(fidelidade_repository, "saldo", lambda db, c, s, marca: estado.saldo)
    monkeypatch.setattr(fidelidade_repository, "valor_do_resgate", lambda db, c, s, marca, p: valor(p))
    monkeypatch.setattr(fidelidade_repository, "valor_gasto",
                        lambda db, id_movimento: estado.gasto if estado.gasto is not None else valor(estado.pedido))
    return estado


def test_resgate_vale_os_pontos_mais_antigos_primeiro(pontos):
    simulado = fidelidade.simular_resgate(SessaoFalsa(), ID_CLIENTE, None, "vulto", 500)
    assert (simulado["desconto"], simulado["saldo"]) == (Decimal("45.00"), 1000)  # 400 × 0,10 + 100 × 0,05


@pytest.mark.parametrize("quantos, valor_itens, erro", [
    (50, None, "mínimo é de 100"), (1500, None, "saldo é de 1000"), (1000, Decimal("70.00"), "menor que o valor"),
])
def test_resgate_recusado(pontos, quantos, valor_itens, erro):
    with pytest.raises(RegraDeNegocio, match=erro):
        fidelidade.simular_resgate(SessaoFalsa(), ID_CLIENTE, None, "vulto", quantos, valor_itens)


def test_resgate_grava_o_desconto_e_baixa_o_total(pontos):
    pedido = Pedido(id_pedido=1, id_cliente=ID_CLIENTE, valor_total=Decimal("219.90"), valor_desconto=Decimal("0"))
    pontos.pedido = 500
    db = SessaoFalsa()

    desconto = fidelidade.resgatar(db, pedido, "vulto", 500, Decimal("200.00"))

    [movimento] = [m for m in db.adicionados if isinstance(m, MovimentoPontos)]
    assert (movimento.tipo, movimento.pontos, movimento.origem, movimento.id_pedido) == (
        "resgate", -500, "plataforma", 1)
    assert (desconto, pedido.valor_desconto, pedido.valor_total) == (
        Decimal("45.00"), Decimal("45.00"), Decimal("174.90"))


def test_resgate_recusa_se_o_saldo_mudou_no_meio(pontos):
    pontos.pedido, pontos.gasto = 500, Decimal("30.00")
    pedido = Pedido(id_pedido=1, id_cliente=ID_CLIENTE, valor_total=Decimal("200.00"), valor_desconto=Decimal("0"))
    with pytest.raises(Conflito, match="saldo de pontos mudou"):
        fidelidade.resgatar(SessaoFalsa(), pedido, "vulto", 500, Decimal("200.00"))


def test_simulacao_no_caixa_acha_o_cliente_da_vulto_sem_conta(api, pontos, monkeypatch):
    monkeypatch.setattr(usuario_repository, "buscar_cliente_por_cpf", lambda db, cpf: None)
    monkeypatch.setattr(cliente_sem_conta_repository, "buscar_por_cpf",
                        lambda db, cpf: SimpleNamespace(id_cliente_sem_conta=7))

    resposta = api(SessaoFalsa(), usuario=funcionario()).get(
        "/fidelidade/resgate/caixa?cpf=529.982.247-25&marca=vulto&pontos=500")

    assert resposta.status_code == 200 and resposta.json()["desconto"] == "45.00"


# ---------- saldo, extrato e passivo ----------

def test_passivo_soma_cada_epoca_pelo_valor_dela(monkeypatch):
    monkeypatch.setattr(fidelidade_repository, "passivo_por_valor", lambda db, em=None: [
        {"marca": "vulto", "valor_ponto": Decimal("0.10"), "pontos": 1000},
        {"marca": "vulto", "valor_ponto": Decimal("0.05"), "pontos": 3000},
    ])

    [vulto] = fidelidade.passivo(SessaoFalsa())

    assert (vulto["pontos"], vulto["reais"], len(vulto["por_valor"])) == (4000, Decimal("250.00"), 2)


# o passivo num momento passado, rodado num SQLite em memória (só as colunas usadas): os lotes lançados até ali,
# menos o que saiu deles até ali; a devolução de um resgate volta para o lote
def test_passivo_num_momento_passado():
    banco = create_engine("sqlite://")
    with banco.begin() as c:
        c.execute(text("CREATE TABLE movimento_pontos (id_movimento int, criado_em text)"))
        c.execute(text("CREATE TABLE lote_pontos (id_movimento int, marca text, valor_ponto numeric, pontos int, "
                       "restante int)"))
        c.execute(text("CREATE TABLE consumo_pontos (id_consumo int, id_movimento int, id_lote int, pontos int)"))
        c.execute(text("INSERT INTO movimento_pontos VALUES (:id, :quando)"), [
            dict(id=1, quando="2026-08-30 12:00:00.000000"),  # crédito
            dict(id=2, quando="2026-09-15 12:00:00.000000"),  # resgate de 300
            dict(id=3, quando="2026-09-20 12:00:00.000000"),  # devolução de 100 do resgate
            dict(id=4, quando="2026-10-05 12:00:00.000000"),  # resgate de 200, depois do momento
            dict(id=5, quando="2026-10-03 12:00:00.000000"),  # crédito depois do momento
            dict(id=6, quando="2026-09-01 12:00:00.000000"),  # crédito de 400 que pagou 150 de saldo negativo (K5f)
        ])
        c.execute(text("INSERT INTO lote_pontos VALUES (:id, 'vulto', :valor, :pontos, :restante)"), [
            dict(id=1, valor=0.10, pontos=1000, restante=600), dict(id=5, valor=0.05, pontos=500, restante=500),
            dict(id=6, valor=0.05, pontos=400, restante=250)])
        c.execute(text("INSERT INTO consumo_pontos VALUES (:id, :movimento, 1, :pontos)"), [
            dict(id=1, movimento=2, pontos=300), dict(id=2, movimento=3, pontos=-100),
            dict(id=3, movimento=4, pontos=200)])

    with Session(banco) as db:
        agora = fidelidade_repository.passivo_por_valor(db)
        em_30_09 = fidelidade_repository.passivo_por_valor(db, datetime(2026, 9, 30, 23, 59))

    assert [(l["valor_ponto"], l["pontos"]) for l in agora] == [(Decimal("0.10"), 600), (Decimal("0.05"), 750)]
    assert [(l["valor_ponto"], l["pontos"]) for l in em_30_09] == [(Decimal("0.10"), 800), (Decimal("0.05"), 250)]


# o extrato da conta traz também o histórico de quando a pessoa era cliente sem conta (P2)
def test_extrato_da_conta_inclui_o_historico_de_antes_da_conta():
    filtro = sql(fidelidade_repository._movimentos_do_dono(ID_CLIENTE, None))
    assert "movimento_pontos.id_cliente_sem_conta IN (SELECT cliente_sem_conta.id_cliente_sem_conta" in filtro
    assert "cliente_sem_conta.id_usuario =" in filtro


def test_consulta_da_equipe_exige_um_cliente(api, monkeypatch):
    monkeypatch.setattr(fidelidade_repository, "resumo", lambda db, c, s: [])
    cliente = api(SessaoFalsa(), usuario=funcionario())

    assert cliente.get("/fidelidade/clientes/pontos").status_code == 422
    assert cliente.get("/fidelidade/clientes/pontos?id_cliente_sem_conta=7").json() == {"saldos": []}


def test_pontos_da_equipe_exigem_permissao(api):
    sem_permissao = api(SessaoFalsa(), usuario=funcionario(), permitido=False)
    assert sem_permissao.get("/fidelidade/clientes/pontos?id_cliente_sem_conta=7").status_code == 403
    assert sem_permissao.get("/fidelidade/passivo").status_code == 403


# ---------- lembrete de pontos vencendo (aba de pontos do site) ----------

def cliente_do_site(**campos):
    dados = dict(id_usuario=ID_CLIENTE, tipo_conta="cliente", status_conta="ativa", id_modelo_acesso=None,
                 id_unidade=None, lembrete_pontos_aceito_em=None, lembrete_pontos_oferecido_em=None)
    dados.update(campos)
    return SimpleNamespace(**dados)


def test_get_lembrete_diz_se_o_cliente_aceitou(api):
    aceitou = cliente_do_site(lembrete_pontos_aceito_em=datetime(2026, 10, 1, tzinfo=timezone.utc))

    sem_aceite = api(SessaoFalsa(), usuario=cliente_do_site()).get("/fidelidade/meus-pontos/lembrete")
    com_aceite = api(SessaoFalsa(), usuario=aceitou).get("/fidelidade/meus-pontos/lembrete")

    assert (sem_aceite.json(), com_aceite.json()) == ({"aceita": False}, {"aceita": True})


# ligar pelo site também conta como oferecido: o assistente não oferece de novo
def test_put_lembrete_liga_e_marca_como_oferecido(api):
    db, cliente = SessaoFalsa(), cliente_do_site()

    resposta = api(db, usuario=cliente).put("/fidelidade/meus-pontos/lembrete", json={"aceita": True})

    assert resposta.status_code == 200
    assert resposta.json() == {"aceita": True}
    assert cliente.lembrete_pontos_aceito_em is not None
    assert cliente.lembrete_pontos_oferecido_em is not None
    assert db.commits == 1


def test_put_lembrete_ligado_de_novo_mantem_a_data_do_aceite(api):
    antes = datetime(2026, 10, 1, tzinfo=timezone.utc)
    cliente = cliente_do_site(lembrete_pontos_aceito_em=antes, lembrete_pontos_oferecido_em=antes)

    api(SessaoFalsa(), usuario=cliente).put("/fidelidade/meus-pontos/lembrete", json={"aceita": True})

    assert (cliente.lembrete_pontos_aceito_em, cliente.lembrete_pontos_oferecido_em) == (antes, antes)


def test_put_lembrete_desliga(api):
    antes = datetime(2026, 10, 1, tzinfo=timezone.utc)
    cliente = cliente_do_site(lembrete_pontos_aceito_em=antes, lembrete_pontos_oferecido_em=antes)

    resposta = api(SessaoFalsa(), usuario=cliente).put("/fidelidade/meus-pontos/lembrete", json={"aceita": False})

    assert resposta.json() == {"aceita": False}
    assert cliente.lembrete_pontos_aceito_em is None
    assert cliente.lembrete_pontos_oferecido_em == antes


def test_lembrete_e_so_para_conta_de_cliente(api):
    resposta = api(SessaoFalsa(), usuario=funcionario()).put("/fidelidade/meus-pontos/lembrete", json={"aceita": True})

    assert resposta.status_code == 403
