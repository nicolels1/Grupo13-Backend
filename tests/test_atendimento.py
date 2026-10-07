import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from src.models.atendimento import Chamado, HistoricoChamado
from src.repositories import atendimento_repository, estoque_repository, permissao_repository, unidade_repository
from src.use_cases import atendimento
from src.use_cases.erros import Conflito, RecursoNaoEncontrado, RegraDeNegocio, SemPermissao
from tests.apoio import SessaoFalsa, api, funcionario  # noqa: F401 (api é fixture)

ID_CLIENTE = uuid.UUID("44444444-4444-4444-4444-444444444444")
ID_OUTRO_CLIENTE = uuid.UUID("55555555-5555-5555-5555-555555555555")
ID_COLEGA = uuid.UUID("66666666-6666-6666-6666-666666666666")
AGORA = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)


def cliente(id_usuario=ID_CLIENTE):
    return SimpleNamespace(id_usuario=id_usuario, nome="Marina", tipo_conta="cliente", status_conta="ativa",
                           id_modelo_acesso=None)


def chamado(id_chamado=1, id_cliente=ID_CLIENTE, status="aberto", id_responsavel=None, prioridade=None):
    return Chamado(id_chamado=id_chamado, id_cliente=id_cliente, status=status, id_responsavel=id_responsavel,
                   prioridade=prioridade, categoria="duvida", assunto="Tem M?", descricao="Quero o tamanho M")


def como_saida(c: Chamado) -> dict:
    # o que a consulta do repository devolve: colunas do chamado + nomes + não lidas
    colunas = {coluna.key: getattr(c, coluna.key) for coluna in Chamado.__table__.columns}
    colunas.update(criado_em=colunas["criado_em"] or AGORA, atualizado_em=colunas["atualizado_em"] or AGORA)
    return {**colunas, "cliente": "Marina", "responsavel": None, "mensagens_nao_lidas": 0}


@pytest.fixture
def banco(monkeypatch):
    estado = SimpleNamespace(
        chamados={}, pedidos={}, itens={}, travados=[], lidas=[], filtros=None, mensagens_listadas=None,
        variantes={7}, unidades={20},
        usuarios={ID_COLEGA: SimpleNamespace(id_usuario=ID_COLEGA, status_conta="ativa")},
        atendem={ID_COLEGA},
    )

    def travar(db, id_chamado):
        estado.travados.append(id_chamado)
        return estado.chamados.get(id_chamado)

    def listar_mensagens(db, id_chamado, incluir_internas):
        estado.mensagens_listadas = incluir_internas
        return []

    def listar_chamados(db, lado, limit, offset, **filtros):
        estado.filtros = (lado, filtros)
        return [], 0

    m = monkeypatch.setattr
    m(atendimento_repository, "buscar_chamado", lambda db, i: estado.chamados.get(i))
    m(atendimento_repository, "travar_chamado", travar)
    m(atendimento_repository, "buscar_pedido", lambda db, i: estado.pedidos.get(i))
    m(atendimento_repository, "buscar_item_pedido", lambda db, i: estado.itens.get(i))
    m(atendimento_repository, "detalhar_chamado", lambda db, lado, i: como_saida(
        estado.chamados.get(i) or next(o for o in db.adicionados if isinstance(o, Chamado) and o.id_chamado == i)))
    m(atendimento_repository, "listar_chamados", listar_chamados)
    m(atendimento_repository, "listar_mensagens", listar_mensagens)
    m(atendimento_repository, "marcar_lidas", lambda db, i, id_cliente, lado: estado.lidas.append((i, lado)))
    m(atendimento_repository, "listar_historico", lambda db, i: [])
    m(estoque_repository, "buscar_variante", lambda db, i: object() if i in estado.variantes else None)
    m(unidade_repository, "buscar_unidade", lambda db, i: object() if i in estado.unidades else None)
    m(permissao_repository, "buscar_usuario", lambda db, i: estado.usuarios.get(i))
    m(atendimento, "usuario_tem_permissao", lambda db, usuario, *codigos: usuario.id_usuario in estado.atendem)
    return estado


def historico(db):
    return [(h.campo_alterado, h.valor_anterior, h.valor_novo) for h in db.adicionados
            if isinstance(h, HistoricoChamado)]


# ---------- abrir chamado ----------

def test_cliente_abre_chamado_aberto_sem_responsavel(banco):
    db = SessaoFalsa()

    saida = atendimento.abrir_chamado(db, cliente(), dict(categoria="duvida", assunto="Tem M?", descricao="x",
                                                         id_variante=7, id_unidade=20))

    criado = db.adicionados[0]
    assert (criado.id_cliente, criado.status, criado.id_responsavel) == (ID_CLIENTE, "aberto", None)
    assert saida["id_chamado"] == criado.id_chamado
    assert db.commits == 1


@pytest.mark.parametrize(
    "dados, erro, mensagem",
    [
        (dict(id_pedido=1), RecursoNaoEncontrado, "Pedido"),            # pedido de outro cliente
        (dict(id_pedido=99), RecursoNaoEncontrado, "Pedido"),           # pedido inexistente
        (dict(id_item_pedido=5), RegraDeNegocio, "informe também o pedido"),
        (dict(id_pedido=2, id_item_pedido=6), RecursoNaoEncontrado, "Item"),  # item de outro pedido
        (dict(id_chamado_anterior=8), RecursoNaoEncontrado, "Chamado anterior"),
        (dict(id_variante=99), RecursoNaoEncontrado, "Variante"),
        (dict(id_unidade=99), RecursoNaoEncontrado, "Unidade"),
    ],
)
def test_chamado_so_aponta_para_o_que_e_do_cliente(banco, dados, erro, mensagem):
    banco.pedidos = {1: SimpleNamespace(id_cliente=ID_OUTRO_CLIENTE), 2: SimpleNamespace(id_cliente=ID_CLIENTE)}
    banco.itens = {6: SimpleNamespace(id_pedido=1)}
    banco.chamados[8] = chamado(8, id_cliente=ID_OUTRO_CLIENTE)
    db = SessaoFalsa()

    with pytest.raises(erro, match=mensagem):
        atendimento.abrir_chamado(db, cliente(), dict(categoria="duvida", assunto="abc", descricao="x", **dados))

    assert db.commits == 0


def test_chamado_de_outro_cliente_responde_como_inexistente(banco):
    banco.chamados[1] = chamado(id_cliente=ID_OUTRO_CLIENTE)

    with pytest.raises(RecursoNaoEncontrado):
        atendimento.detalhar_chamado_do_cliente(SessaoFalsa(), cliente(), 1)


def test_cliente_ve_so_mensagens_publicas_e_marca_as_da_equipe_como_lidas(banco):
    banco.chamados[1] = chamado()

    atendimento.mensagens_do_cliente(SessaoFalsa(), cliente(), 1)

    assert banco.mensagens_listadas is False
    assert banco.lidas == [(1, "cliente")]


def test_mensagem_nova_traz_autor_e_lado(banco):
    banco.chamados[1] = chamado()
    db = SessaoFalsa()

    saida = atendimento.enviar_mensagem_do_cliente(db, cliente(), 1, "Alguma novidade?")

    assert (saida["conteudo"], saida["autor"], saida["da_equipe"], saida["interna"]) == (
        "Alguma novidade?", "Marina", False, False
    )


def test_chamado_concluido_nao_recebe_mensagem(banco):
    banco.chamados[1] = chamado(status="concluido")

    with pytest.raises(RegraDeNegocio, match="não há reabertura"):
        atendimento.enviar_mensagem_do_cliente(SessaoFalsa(), cliente(), 1, "oi")


# ---------- equipe ----------

def test_assumir_define_responsavel_e_registra_historico(banco):
    banco.chamados[1] = chamado()
    db, eu = SessaoFalsa(), funcionario()

    atendimento.assumir(db, eu, 1)

    c = banco.chamados[1]
    assert (c.id_responsavel, c.status) == (eu.id_usuario, "em_andamento")
    assert c.assumido_em is not None
    assert historico(db) == [("responsavel", None, str(eu.id_usuario)), ("status", "aberto", "em_andamento")]
    assert banco.travados == [1]  # travado antes de conferir o responsável
    assert db.commits == 1


def test_chamado_com_responsavel_nao_e_assumido_por_outro(banco):
    banco.chamados[1] = chamado(status="em_andamento", id_responsavel=ID_COLEGA)

    with pytest.raises(Conflito, match="já tem responsável"):
        atendimento.assumir(SessaoFalsa(), funcionario(), 1)


def test_chamado_concluido_nao_e_assumido(banco):
    banco.chamados[1] = chamado(status="concluido")

    with pytest.raises(RegraDeNegocio):
        atendimento.assumir(SessaoFalsa(), funcionario(), 1)


@pytest.mark.parametrize("acao", [
    lambda db, u: atendimento.alterar(db, u, 1, {"prioridade": "alta"}),
    lambda db, u: atendimento.concluir(db, u, 1, "resolvido"),
])
def test_so_o_responsavel_altera_ou_conclui(banco, acao):
    banco.chamados[1] = chamado(status="em_andamento", id_responsavel=ID_COLEGA)

    with pytest.raises(SemPermissao, match="responsável"):
        acao(SessaoFalsa(), funcionario())


def test_responsavel_define_prioridade_com_historico(banco):
    eu = funcionario()
    banco.chamados[1] = chamado(status="em_andamento", id_responsavel=eu.id_usuario)
    db = SessaoFalsa()

    atendimento.alterar(db, eu, 1, {"prioridade": "alta"})
    atendimento.alterar(db, eu, 1, {"prioridade": "alta"})  # igual: nada muda

    assert banco.chamados[1].prioridade == "alta"
    assert historico(db) == [("prioridade", None, "alta")]


def test_repasse_para_quem_atende_chamados(banco):
    eu = funcionario()
    banco.chamados[1] = chamado(status="em_andamento", id_responsavel=eu.id_usuario)
    db = SessaoFalsa()

    atendimento.alterar(db, eu, 1, {"id_responsavel": ID_COLEGA})

    assert banco.chamados[1].id_responsavel == ID_COLEGA
    assert historico(db) == [("responsavel", str(eu.id_usuario), str(ID_COLEGA))]


@pytest.mark.parametrize("destino", ["sem_permissao", "inativo", "inexistente"])
def test_repasse_para_quem_nao_pode_atender_e_recusado(banco, destino):
    eu = funcionario()
    banco.chamados[1] = chamado(status="em_andamento", id_responsavel=eu.id_usuario)
    alvo = uuid.uuid4()
    if destino != "inexistente":
        banco.usuarios[alvo] = SimpleNamespace(id_usuario=alvo, status_conta="ativa" if destino == "sem_permissao" else "inativa")
    if destino == "inativo":
        banco.atendem.add(alvo)

    with pytest.raises(RegraDeNegocio, match="repassado"):
        atendimento.alterar(SessaoFalsa(), eu, 1, {"id_responsavel": alvo})

    assert banco.chamados[1].id_responsavel == eu.id_usuario


def test_concluir_registra_motivo_e_historico(banco):
    eu = funcionario()
    banco.chamados[1] = chamado(status="em_andamento", id_responsavel=eu.id_usuario)
    db = SessaoFalsa()

    atendimento.concluir(db, eu, 1, "resolvido")

    c = banco.chamados[1]
    assert (c.status, c.motivo_encerramento) == ("concluido", "resolvido")
    assert c.concluido_em is not None
    assert historico(db) == [("status", "em_andamento", "concluido")]


def test_concluido_nao_volta(banco):
    eu = funcionario()
    banco.chamados[1] = chamado(status="concluido", id_responsavel=eu.id_usuario)

    with pytest.raises(RegraDeNegocio, match="não há reabertura"):
        atendimento.alterar(SessaoFalsa(), eu, 1, {"prioridade": "baixa"})


def test_equipe_ve_mensagens_internas_e_marca_as_do_cliente_como_lidas(banco):
    banco.chamados[1] = chamado()

    atendimento.mensagens_da_equipe(SessaoFalsa(), 1)

    assert banco.mensagens_listadas is True
    assert banco.lidas == [(1, "equipe")]


def test_mensagem_interna_da_equipe(banco):
    banco.chamados[1] = chamado()

    saida = atendimento.enviar_mensagem_da_equipe(SessaoFalsa(), funcionario(nome="Juliana"), 1, "ver estoque", True)

    assert (saida["interna"], saida["da_equipe"]) == (True, True)


def test_meus_chamados_filtra_pelo_responsavel(banco):
    eu = funcionario()

    atendimento.listar_fila(SessaoFalsa(), eu, 10, 0, meus=True, status="em_andamento")

    assert banco.filtros == ("equipe", {"status": "em_andamento", "id_responsavel": eu.id_usuario})


# ---------- consultas ----------

def sql(consulta):
    return str(consulta.compile(dialect=postgresql.dialect()))


def test_nao_lidas_contam_o_outro_lado():
    equipe = sql(atendimento_repository.consulta_chamados("equipe"))
    do_cliente = sql(atendimento_repository.consulta_chamados("cliente"))

    assert "mensagem.id_autor = chamado.id_cliente" in equipe
    assert "mensagem.id_autor != chamado.id_cliente" in do_cliente
    assert "mensagem.interna IS false" in do_cliente  # interna nunca conta para o cliente


def test_cliente_nunca_recebe_mensagem_interna():
    assert "mensagem.interna IS false" in sql(atendimento_repository.consulta_mensagens(1, incluir_internas=False))
    assert "interna IS false" not in sql(atendimento_repository.consulta_mensagens(1, incluir_internas=True))


# ---------- rotas ----------

CHAMADO = {"categoria": "duvida", "assunto": "Tem M?", "descricao": "Quero o M"}


def test_conta_interna_nao_usa_as_rotas_do_cliente(api, banco):
    resposta = api(SessaoFalsa(), usuario=funcionario()).post("/chamados", json=CHAMADO)

    assert resposta.status_code == 403
    assert resposta.json() == {"detail": "Só para contas de cliente"}


def test_cliente_abre_chamado_pela_api(api, banco):
    resposta = api(SessaoFalsa(), usuario=cliente()).post("/chamados", json=CHAMADO)

    assert resposta.status_code == 201
    assert resposta.json()["status"] == "aberto"
    assert resposta.json()["id_cliente"] == str(ID_CLIENTE)


def test_categoria_fora_da_lista_responde_422(api, banco):
    resposta = api(SessaoFalsa(), usuario=cliente()).post("/chamados", json={**CHAMADO, "categoria": "reclamacao"})

    assert resposta.status_code == 422


def test_cliente_nao_abre_chamado_de_outra_pessoa_pela_url(api, banco):
    banco.chamados[3] = chamado(3, id_cliente=ID_OUTRO_CLIENTE)

    resposta = api(SessaoFalsa(), usuario=cliente()).get("/chamados/3")

    assert resposta.status_code == 404


def test_fila_exige_atender_chamado(api, banco):
    resposta = api(SessaoFalsa(), usuario=funcionario(), permitido=False).get("/atendimento/chamados")

    assert resposta.status_code == 403


def test_cliente_nao_ve_a_fila(api, banco):
    resposta = api(SessaoFalsa(), usuario=cliente()).get("/atendimento/chamados")

    assert resposta.status_code == 403


def test_assumir_pela_api(api, banco):
    banco.chamados[1] = chamado()

    resposta = api(SessaoFalsa(), usuario=funcionario()).post("/atendimento/chamados/1/assumir")

    assert resposta.status_code == 200
    assert resposta.json()["status"] == "em_andamento"


def test_assumir_chamado_ja_assumido_responde_409(api, banco):
    banco.chamados[1] = chamado(status="em_andamento", id_responsavel=ID_COLEGA)

    resposta = api(SessaoFalsa(), usuario=funcionario()).post("/atendimento/chamados/1/assumir")

    assert resposta.status_code == 409


def test_motivo_de_conclusao_fora_da_lista_responde_422(api, banco):
    resposta = api(SessaoFalsa(), usuario=funcionario()).post(
        "/atendimento/chamados/1/concluir", json={"motivo": "cancelado"}
    )

    assert resposta.status_code == 422


def pessoa(id_usuario, nome, id_unidade=None):
    return SimpleNamespace(id_usuario=id_usuario, nome=nome, id_unidade=id_unidade, tipo_conta="interna",
                           status_conta="ativa")


def test_equipe_traz_so_quem_atende_chamados(banco, monkeypatch):
    sem_permissao = pessoa(uuid.UUID("77777777-7777-7777-7777-777777777777"), "Bruno")
    colega = pessoa(ID_COLEGA, "Juliana", id_unidade=20)
    monkeypatch.setattr(atendimento_repository, "contas_internas_ativas", lambda db: [sem_permissao, colega])

    assert atendimento.equipe(SessaoFalsa()) == [colega]


def test_equipe_pela_api(api, banco, monkeypatch):
    monkeypatch.setattr(atendimento_repository, "contas_internas_ativas", lambda db: [pessoa(ID_COLEGA, "Juliana", 20)])

    resposta = api(SessaoFalsa(), usuario=funcionario()).get("/atendimento/equipe")

    assert resposta.status_code == 200
    assert resposta.json() == {"items": [{"id_usuario": str(ID_COLEGA), "nome": "Juliana", "id_unidade": 20}]}


def test_equipe_exige_atender_chamado(api, banco):
    resposta = api(SessaoFalsa(), usuario=funcionario(), permitido=False).get("/atendimento/equipe")

    assert resposta.status_code == 403
