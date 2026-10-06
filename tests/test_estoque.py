import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from src.app import app
from src.database.session import get_db
from src.middlewares.permissoes import get_usuario_ativo
from src.repositories import estoque_repository, permissao_repository
from src.use_cases import estoque
from src.use_cases.erros import RecursoNaoEncontrado, RegraDeNegocio

BRASILIA = ZoneInfo("America/Sao_Paulo")
USUARIO = SimpleNamespace(id_usuario=uuid.UUID("11111111-1111-1111-1111-111111111111"))


# ---------- regras puras ----------

@pytest.mark.parametrize(
    "tipo, quantidade, esperado",
    [("recebimento", 5, 5), ("avaria", 2, -2), ("perda", 3, -3), ("ajuste", -4, -4), ("ajuste", 4, 4)],
)
def test_sinal_vem_do_tipo(tipo, quantidade, esperado):
    assert estoque.quantidade_com_sinal(tipo, quantidade) == esperado


@pytest.mark.parametrize("tipo, quantidade", [("recebimento", 0), ("ajuste", 0), ("avaria", -2), ("recebimento", -1)])
def test_quantidade_zero_ou_negativa_fora_do_ajuste_e_recusada(tipo, quantidade):
    with pytest.raises(RegraDeNegocio):
        estoque.quantidade_com_sinal(tipo, quantidade)


def test_data_sem_hora_vale_o_fim_do_dia_em_brasilia():
    momento = estoque.interpretar_momento("2026-09-22")

    assert momento == datetime(2026, 9, 22, 23, 59, 59, 999999, tzinfo=BRASILIA)


def test_data_sem_hora_no_inicio_de_intervalo_vale_o_comeco_do_dia():
    assert estoque.interpretar_momento("2026-09-22", fim_do_dia=False) == datetime(2026, 9, 22, tzinfo=BRASILIA)


def test_hora_sem_fuso_e_de_brasilia_e_com_fuso_e_respeitada():
    assert estoque.interpretar_momento("2026-09-22T10:30") == datetime(2026, 9, 22, 10, 30, tzinfo=BRASILIA)
    assert estoque.interpretar_momento("2026-09-22T10:30+00:00") == datetime(2026, 9, 22, 10, 30, tzinfo=timezone.utc)


@pytest.mark.parametrize("texto", ["22/09/2026", "ontem", "2026-13-01"])
def test_data_invalida_responde_com_formato_esperado(texto):
    with pytest.raises(RegraDeNegocio, match="AAAA-MM-DD"):
        estoque.interpretar_momento(texto)


@pytest.mark.parametrize(
    "duracao, esperada",
    [(timedelta(hours=24), "hora"), (timedelta(days=2), "hora"), (timedelta(days=30), "dia"),
     (timedelta(days=90), "dia"), (timedelta(days=91), "semana")],
)
def test_granularidade_automatica(duracao, esperada):
    fim = datetime(2026, 10, 6, tzinfo=timezone.utc)
    assert estoque.escolher_granularidade(fim - duracao, fim) == esperada


def test_semana_comeca_na_segunda_como_no_postgres():
    quarta = datetime(2026, 10, 7, 15, 40)
    assert estoque.inicio_do_periodo(quarta, "semana") == datetime(2026, 10, 5)
    assert estoque.inicio_do_periodo(quarta, "dia") == datetime(2026, 10, 7)
    assert estoque.inicio_do_periodo(quarta, "hora") == datetime(2026, 10, 7, 15)


def test_periodos_cobrem_o_intervalo_no_horario_de_brasilia():
    inicio = datetime(2026, 10, 1, 3, 0, tzinfo=timezone.utc)  # 00:00 em Brasília
    fim = datetime(2026, 10, 3, 2, 59, tzinfo=timezone.utc)  # 23:59 do dia 2 em Brasília

    assert estoque.periodos(inicio, fim, "dia") == [datetime(2026, 10, 1), datetime(2026, 10, 2)]


def test_periodo_longo_demais_para_a_granularidade_e_recusado():
    fim = datetime(2026, 10, 6, tzinfo=timezone.utc)
    with pytest.raises(RegraDeNegocio, match="granularidade"):
        estoque.periodos(fim - timedelta(days=60), fim, "hora")


# ---------- use cases com banco falso ----------

class SessaoFalsa:
    def __init__(self):
        self.commits = 0
        self.adicionados = []

    def commit(self):
        self.commits += 1


def linha(quantidade, reservada=0):
    return SimpleNamespace(quantidade=quantidade, quantidade_reservada=reservada)


@pytest.fixture
def banco(monkeypatch):
    estado = SimpleNamespace(
        variantes={1: SimpleNamespace(ativo=True), 2: SimpleNamespace(ativo=False)},
        unidades={
            10: SimpleNamespace(tipo="cd", ativo=True),
            20: SimpleNamespace(tipo="loja", ativo=True),
            30: SimpleNamespace(tipo="loja", ativo=False),
        },
        estoque={},
        travados=[],
        inseridas=[],
        adicionados=[],
    )

    def travar(db, id_variante, id_unidade, canais):
        estado.travados.append(list(canais))
        return {c: estado.estoque[(id_variante, id_unidade, c)] for c in canais
                if (id_variante, id_unidade, c) in estado.estoque}

    def inserir(db, **campos):
        estado.inseridas.append(campos)
        return SimpleNamespace(**campos)

    monkeypatch.setattr(estoque_repository, "buscar_variante", lambda db, i: estado.variantes.get(i))
    monkeypatch.setattr(estoque_repository, "buscar_unidade", lambda db, i: estado.unidades.get(i))
    monkeypatch.setattr(estoque_repository, "travar_estoque", travar)
    monkeypatch.setattr(estoque_repository, "inserir_movimentacao", inserir)
    monkeypatch.setattr(estoque_repository, "buscar_estoque", lambda db, v, u, c: estado.estoque.get((v, u, c)))
    monkeypatch.setattr(estoque_repository, "adicionar_estoque", lambda db, e: estado.adicionados.append(e))
    monkeypatch.setattr(estoque_repository, "listar_estoque", lambda db, limit, offset, **f: ([{"linha": f}], 1))
    return estado


def mover(db, tipo="recebimento", quantidade=5, unidade=20, canal="loja_fisica", motivo=None, variante=1):
    return estoque.registrar_movimentacao(db, USUARIO, variante, unidade, canal, tipo, quantidade, motivo)


def test_recebimento_insere_movimentacao_com_autor(banco):
    db = SessaoFalsa()

    mover(db, quantidade=5)

    assert banco.inseridas == [dict(
        id_variante=1, id_unidade=20, canal="loja_fisica", id_usuario=USUARIO.id_usuario,
        tipo="recebimento", quantidade=5, motivo=None,
    )]
    assert db.commits == 1
    assert banco.travados == []  # entrada não precisa conferir o disponível


def test_avaria_sai_do_disponivel_com_motivo(banco):
    banco.estoque[(1, 20, "loja_fisica")] = linha(5)

    mover(SessaoFalsa(), tipo="avaria", quantidade=2, motivo="  manchada  ")

    assert banco.inseridas[0]["quantidade"] == -2
    assert banco.inseridas[0]["motivo"] == "manchada"
    assert banco.travados == [["loja_fisica"]]


@pytest.mark.parametrize("tipo", ["avaria", "perda", "ajuste"])
def test_motivo_obrigatorio(banco, tipo):
    with pytest.raises(RegraDeNegocio, match="Motivo obrigatório"):
        mover(SessaoFalsa(), tipo=tipo, quantidade=1, motivo="   ")


def test_saida_nao_passa_do_disponivel(banco):
    # 5 em estoque, 4 reservadas para pedidos online: só 1 disponível
    banco.estoque[(1, 20, "online")] = linha(5, reservada=4)

    with pytest.raises(RegraDeNegocio, match="há 1 peça"):
        mover(SessaoFalsa(), tipo="perda", quantidade=2, canal="online", motivo="sumiu")

    assert banco.inseridas == []


def test_saida_sem_linha_de_estoque_tem_zero_disponivel(banco):
    with pytest.raises(RegraDeNegocio, match="há 0 peça"):
        mover(SessaoFalsa(), tipo="ajuste", quantidade=-1, motivo="contagem")


@pytest.mark.parametrize(
    "campos, erro, mensagem",
    [
        (dict(variante=99), RecursoNaoEncontrado, "Variante"),
        (dict(unidade=99), RecursoNaoEncontrado, "Unidade"),
        (dict(unidade=30), RegraDeNegocio, "Unidade desativada"),
        (dict(unidade=10, canal="loja_fisica"), RegraDeNegocio, "CD só tem estoque online"),
        (dict(variante=2), RegraDeNegocio, "Variante desativada"),
    ],
)
def test_local_invalido_e_recusado(banco, campos, erro, mensagem):
    with pytest.raises(erro, match=mensagem):
        mover(SessaoFalsa(), **campos)


def test_realocacao_registra_saida_e_entrada_juntas(banco):
    banco.estoque[(1, 20, "online")] = linha(3)
    db = SessaoFalsa()

    estoque.realocar(db, USUARIO, 1, 20, "online", 2)

    assert [(m["canal"], m["tipo"], m["quantidade"]) for m in banco.inseridas] == [
        ("online", "saida_realocacao", -2), ("loja_fisica", "entrada_realocacao", 2),
    ]
    assert banco.travados == [["loja_fisica", "online"]]  # sempre na mesma ordem
    assert db.commits == 1


def test_realocacao_no_cd_e_recusada(banco):
    with pytest.raises(RegraDeNegocio, match="CD só tem estoque online"):
        estoque.realocar(SessaoFalsa(), USUARIO, 1, 10, "online", 1)


def test_realocacao_nao_passa_do_disponivel(banco):
    banco.estoque[(1, 20, "loja_fisica")] = linha(2, reservada=0)

    with pytest.raises(RegraDeNegocio, match="há 2 peça"):
        estoque.realocar(SessaoFalsa(), USUARIO, 1, 20, "loja_fisica", 3)

    assert banco.inseridas == []


def test_minimo_cria_a_linha_se_ainda_nao_existe(banco):
    db = SessaoFalsa()

    estoque.definir_minimo(db, USUARIO, 1, 20, "loja_fisica", 4)

    criada = banco.adicionados[0]
    assert (criada.id_variante, criada.id_unidade, criada.canal, criada.estoque_minimo) == (1, 20, "loja_fisica", 4)
    assert criada.minimo_alterado_por == USUARIO.id_usuario
    assert db.commits == 1


def test_minimo_atualiza_linha_existente(banco):
    existente = SimpleNamespace(estoque_minimo=None, minimo_alterado_por=None, minimo_alterado_em=None)
    banco.estoque[(1, 20, "online")] = existente

    estoque.definir_minimo(SessaoFalsa(), USUARIO, 1, 20, "online", 6)

    assert existente.estoque_minimo == 6
    assert banco.adicionados == []


def test_evolucao_acumula_o_saldo_por_periodo(banco, monkeypatch):
    monkeypatch.setattr(estoque_repository, "saldo_antes", lambda *a: {"online": 10})
    monkeypatch.setattr(estoque_repository, "somas_por_periodo", lambda *a: [
        (datetime(2026, 10, 2), "online", -3), (datetime(2026, 10, 2), "loja_fisica", 4),
        (datetime(2026, 10, 3), "online", 5),
    ])

    resultado = estoque.evolucao(None, 1, None, "2026-10-01", "2026-10-03", None)

    # 01 a 03 inclusive passa de 2 dias: um ponto por dia, com o saldo no fim de cada um
    assert resultado["granularidade"] == "dia"
    pontos = [(p["inicio_periodo"], p["loja_fisica"], p["online"]) for p in resultado["pontos"]]
    assert pontos == [
        (datetime(2026, 10, 1, tzinfo=BRASILIA), 0, 10),
        (datetime(2026, 10, 2, tzinfo=BRASILIA), 4, 7),
        (datetime(2026, 10, 3, tzinfo=BRASILIA), 4, 12),
    ]


def test_evolucao_por_dia(banco, monkeypatch):
    monkeypatch.setattr(estoque_repository, "saldo_antes", lambda *a: {})
    monkeypatch.setattr(estoque_repository, "somas_por_periodo", lambda *a: [(datetime(2026, 9, 15), "online", 8)])

    resultado = estoque.evolucao(None, 1, 20, "2026-09-01", "2026-09-30", None)

    assert resultado["granularidade"] == "dia"
    assert len(resultado["pontos"]) == 30
    assert resultado["pontos"][13]["online"] == 0 and resultado["pontos"][14]["online"] == 8
    assert resultado["pontos"][-1]["online"] == 8


def test_evolucao_com_inicio_depois_do_fim_e_recusada(banco):
    with pytest.raises(RegraDeNegocio, match="antes do fim"):
        estoque.evolucao(None, 1, None, "2026-10-05", "2026-10-01", None)


def test_evolucao_de_variante_inexistente(banco):
    with pytest.raises(RecursoNaoEncontrado):
        estoque.evolucao(None, 99, None, None, None, None)


# ---------- consultas: o SQL monta para o PostgreSQL ----------

def sql(consulta):
    return str(consulta.compile(dialect=postgresql.dialect()))


def test_consultas_montam_sem_erro():
    agora = datetime.now(timezone.utc)
    for consulta in [
        estoque_repository.consulta_estoque(id_unidade=1, canal="online", busca="cam", abaixo_minimo=True),
        estoque_repository.consulta_historico(agora, id_variante=1),
        estoque_repository.consulta_movimentacoes(tipo="ajuste", de=agora, ate=agora),
        estoque_repository.consulta_divergencias(),
    ]:
        assert sql(consulta).startswith("SELECT")


def test_historico_soma_so_ate_o_momento_pedido():
    texto = sql(estoque_repository.consulta_historico(datetime.now(timezone.utc)))
    assert "sum(movimentacao_estoque.quantidade)" in texto
    assert "movimentacao_estoque.criado_em <=" in texto


@pytest.mark.parametrize("granularidade, unidade", [("hora", "hour"), ("dia", "day"), ("semana", "week")])
def test_periodo_no_horario_de_brasilia_e_igual_no_select_e_no_group_by(granularidade, unidade):
    agora = datetime.now(timezone.utc)
    texto = sql(estoque_repository.consulta_somas_por_periodo(1, None, agora, agora, granularidade))
    expressao = f"date_trunc('{unidade}', timezone('America/Sao_Paulo', movimentacao_estoque.criado_em))"
    assert texto.count(expressao) == 2


# ---------- rotas e permissões ----------

@pytest.fixture
def client(monkeypatch, banco):
    def logar(codigos):
        usuario = SimpleNamespace(
            id_usuario=USUARIO.id_usuario, tipo_conta="interna", id_modelo_acesso=2, status_conta="ativa"
        )
        app.dependency_overrides[get_usuario_ativo] = lambda: usuario
        monkeypatch.setattr(permissao_repository, "modelo_eh_admin", lambda db, i: False)
        monkeypatch.setattr(permissao_repository, "codigos_do_modelo", lambda db, i: set(codigos))
        monkeypatch.setattr(permissao_repository, "excecoes_do_usuario", lambda db, i: {})
        return TestClient(app)

    app.dependency_overrides[get_db] = lambda: SessaoFalsa()
    yield logar
    app.dependency_overrides.clear()


def test_atendente_nao_ve_o_estoque(client):
    resposta = client({"atender_chamado", "moderar_avaliacoes"}).get("/estoque")

    assert resposta.status_code == 403


def test_quem_so_solicita_transferencia_ve_o_estoque(client, monkeypatch):
    monkeypatch.setattr(estoque_repository, "listar_estoque", lambda db, limit, offset, **f: ([], 0))

    resposta = client({"solicitar_transferencia"}).get("/estoque?limit=10")

    assert resposta.status_code == 200
    assert resposta.json() == {"items": [], "total": 0, "limit": 10, "offset": 0}


def test_ver_estoque_nao_permite_movimentar(client):
    resposta = client({"definir_estoque_minimo"}).post(
        "/movimentacoes-estoque",
        json={"id_variante": 1, "id_unidade": 20, "canal": "loja_fisica", "tipo": "recebimento", "quantidade": 1},
    )

    assert resposta.status_code == 403


def test_tipo_que_nasce_de_fluxo_nao_e_aceito_a_mao(client):
    resposta = client({"movimentar_estoque"}).post(
        "/movimentacoes-estoque",
        json={"id_variante": 1, "id_unidade": 20, "canal": "loja_fisica", "tipo": "venda", "quantidade": 1},
    )

    assert resposta.status_code == 422


def test_regra_do_estoque_volta_como_422_com_mensagem(client, banco):
    resposta = client({"movimentar_estoque"}).post(
        "/movimentacoes-estoque",
        json={"id_variante": 1, "id_unidade": 20, "canal": "loja_fisica", "tipo": "perda", "quantidade": 1,
              "motivo": "sumiu"},
    )

    assert resposta.status_code == 422
    assert resposta.json() == {"detail": "Estoque disponível insuficiente: há 0 peça(s) disponível(is)"}


@pytest.mark.parametrize("limit", [0, 201])
def test_limite_da_paginacao(client, limit):
    assert client({"movimentar_estoque"}).get(f"/estoque?limit={limit}").status_code == 422


def test_historico_com_data_invalida(client):
    resposta = client({"movimentar_estoque"}).get("/estoque/historico?em=22/09/2026")

    assert resposta.status_code == 422
    assert "AAAA-MM-DD" in resposta.json()["detail"]


# ---------- peças em trânsito ----------

def test_em_transito_e_o_que_saiu_e_ainda_nao_chegou():
    texto = sql(estoque_repository.consulta_em_transito(datetime.now(timezone.utc)))

    assert "transferencia.enviada_em <=" in texto
    assert "transferencia.recebida_em IS NULL OR transferencia.recebida_em >" in texto
    assert "item_transferencia.quantidade_enviada >" in texto


def test_em_transito_sem_data_usa_agora_e_com_data_o_fim_do_dia(monkeypatch):
    momentos = []
    monkeypatch.setattr(estoque_repository, "listar_em_transito", lambda db, em, **f: momentos.append(em) or [])

    estoque.em_transito(None, None)
    estoque.em_transito(None, "2026-09-22")

    assert (datetime.now(timezone.utc) - momentos[0]).total_seconds() < 5
    assert momentos[1] == datetime(2026, 9, 22, 23, 59, 59, 999999, tzinfo=BRASILIA)


def test_atendente_nao_ve_pecas_em_transito(client):
    assert client({"atender_chamado"}).get("/estoque/em-transito").status_code == 403


def test_em_transito_pela_api(client, monkeypatch):
    monkeypatch.setattr(estoque_repository, "listar_em_transito", lambda db, em, **f: [])

    resposta = client({"receber_transferencia"}).get("/estoque/em-transito?em=2026-09-22&id_unidade=20")

    assert resposta.status_code == 200
    assert resposta.json() == {"items": []}
