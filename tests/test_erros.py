import logging
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from src.config.logs import AdicionaRequestId, request_id_atual
from src.middlewares.erros import configurar_erros
from src.middlewares.requisicao import configurar_requisicao
from src.use_cases.erros import Conflito, RecursoNaoEncontrado, RegraDeNegocio, SemPermissao


class ErroDoBancoFalso(Exception):
    # imita o erro do psycopg: código SQLSTATE e diagnóstico com a mensagem e a restrição
    def __init__(self, sqlstate, mensagem, restricao=None):
        super().__init__(mensagem)
        self.sqlstate = sqlstate
        self.diag = SimpleNamespace(message_primary=mensagem, constraint_name=restricao)


def erro_do_banco(sqlstate, mensagem, restricao=None):
    return IntegrityError("INSERT ...", {}, ErroDoBancoFalso(sqlstate, mensagem, restricao))


@pytest.fixture
def client():
    # app mínimo com os mesmos tratadores do app.py e uma rota que lança o erro pedido
    app = FastAPI()
    configurar_erros(app)
    configurar_requisicao(app)
    erros = {}

    @app.get("/falha/{nome}")
    def falha(nome: str):
        raise erros[nome]

    def lancar(erro):
        erros["atual"] = erro
        return TestClient(app).get("/falha/atual")

    return lancar


@pytest.mark.parametrize(
    "erro, status_code",
    [
        (RecursoNaoEncontrado("Chamado não encontrado"), 404),
        (SemPermissao("Sem permissão"), 403),
        (Conflito("Chamado já assumido"), 409),
        (RegraDeNegocio("Estoque insuficiente"), 422),
    ],
)
def test_erro_de_negocio_vira_status_da_classe(client, erro, status_code):
    resposta = client(erro)

    assert resposta.status_code == status_code
    assert resposta.json() == {"detail": erro.mensagem}


def test_mensagem_do_trigger_chega_ao_usuario(client):
    resposta = client(erro_do_banco("23514", "Estoque insuficiente: saldo 2, movimentação -5"))

    assert resposta.status_code == 422
    assert resposta.json() == {"detail": "Estoque insuficiente: saldo 2, movimentação -5"}


def test_check_da_tabela_nao_expoe_nome_da_restricao(client):
    erro = erro_do_banco(
        "23514", 'new row for relation "estoque" violates check constraint', "ck_estoque_canal_valido"
    )

    resposta = client(erro)

    assert resposta.status_code == 422
    assert resposta.json() == {"detail": "Dados inválidos"}


@pytest.mark.parametrize(
    "sqlstate, status_code, mensagem",
    [
        ("23505", 409, "Registro já existe"),
        ("23503", 422, "Registro relacionado não existe ou ainda está em uso"),
        ("23502", 422, "Campo obrigatório ausente"),
    ],
)
def test_restricoes_do_banco_viram_mensagem_generica(client, sqlstate, status_code, mensagem):
    resposta = client(erro_do_banco(sqlstate, "detalhe interno do banco", "uq_usuario_email"))

    assert resposta.status_code == status_code
    assert resposta.json() == {"detail": mensagem}


def test_erro_de_banco_desconhecido_vira_500(client):
    resposta = client(erro_do_banco("23P01", "detalhe interno do banco"))

    assert resposta.status_code == 500
    assert resposta.json() == {"detail": "Erro interno do servidor"}


def test_erro_inesperado_vira_500_sem_detalhe(client):
    resposta = client(RuntimeError("senha=123 no meio da mensagem"))

    assert resposta.status_code == 500
    assert resposta.json() == {"detail": "Erro interno do servidor"}
    assert "senha" not in resposta.text


def test_toda_resposta_tem_request_id(client):
    sucesso = client(RecursoNaoEncontrado("x"))
    falha = client(RuntimeError("x"))

    assert len(sucesso.headers["X-Request-ID"]) == 8
    assert len(falha.headers["X-Request-ID"]) == 8
    assert sucesso.headers["X-Request-ID"] != falha.headers["X-Request-ID"]


def test_request_id_do_header_e_o_mesmo_dos_logs():
    app = FastAPI()
    configurar_requisicao(app)

    @app.get("/id")
    def id_da_requisicao():
        return {"request_id": request_id_atual.get()}

    resposta = TestClient(app).get("/id")

    assert resposta.json()["request_id"] == resposta.headers["X-Request-ID"]
    assert request_id_atual.get() == "-"  # fora de uma requisição


def test_filtro_de_log_adiciona_o_request_id():
    registro = logging.LogRecord("src.teste", logging.INFO, __file__, 1, "msg", None, None)
    marcador = request_id_atual.set("abc12345")
    try:
        AdicionaRequestId().filter(registro)
    finally:
        request_id_atual.reset(marcador)

    assert registro.request_id == "abc12345"
