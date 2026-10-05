import uuid
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from src.database.session import get_db
from src.middlewares import permissoes
from src.middlewares.auth import get_current_user
from src.use_cases.permissoes import tem_permissao

USER_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
MODELO_ESTOQUISTA = 2
MODELO_ADMIN = 1


# ---------- lógica pura: permissão efetiva ----------

def checar(codigo, eh_admin=False, do_modelo=(), acrescentadas=(), retiradas=()):
    return tem_permissao(
        codigo,
        eh_admin=eh_admin,
        do_modelo=set(do_modelo),
        acrescentadas=set(acrescentadas),
        retiradas=set(retiradas),
    )


def test_permissao_do_modelo_e_concedida():
    assert checar("movimentar_estoque", do_modelo={"movimentar_estoque"})


def test_permissao_fora_do_modelo_e_negada():
    assert not checar("atender_chamado", do_modelo={"movimentar_estoque"})


def test_excecao_acrescentar_concede():
    assert checar("atender_chamado", do_modelo={"movimentar_estoque"}, acrescentadas={"atender_chamado"})


def test_excecao_retirar_tira_permissao_do_modelo():
    assert not checar("movimentar_estoque", do_modelo={"movimentar_estoque"}, retiradas={"movimentar_estoque"})


def test_admin_tem_permissao_fora_de_qualquer_lista():
    # inclusive permissões futuras, que nem estão cadastradas
    assert checar("permissao_que_ainda_nao_existe", eh_admin=True)


def test_admin_ignora_excecao_retirar():
    assert checar("movimentar_estoque", eh_admin=True, retiradas={"movimentar_estoque"})


@pytest.mark.parametrize("codigo", ["gerenciar_contas", "gerenciar_modelos_acesso", "gerenciar_unidades"])
def test_gestao_nao_e_concedida_por_modelo_nem_excecao(codigo):
    assert not checar(codigo, do_modelo={codigo}, acrescentadas={codigo})
    assert checar(codigo, eh_admin=True)


# ---------- dependências do FastAPI ----------

def fazer_usuario(**campos):
    dados = dict(
        id_usuario=USER_ID,
        tipo_conta="interna",
        status_conta="ativa",
        id_modelo_acesso=MODELO_ESTOQUISTA,
    )
    dados.update(campos)
    return SimpleNamespace(**dados)


@pytest.fixture
def banco(monkeypatch):
    # banco falso: troca as consultas do repositório por dados em memória
    estado = SimpleNamespace(
        usuario=fazer_usuario(),
        admins={MODELO_ADMIN},
        modelos={MODELO_ESTOQUISTA: {"movimentar_estoque"}},
        excecoes={},
    )
    repo = permissoes.repo
    monkeypatch.setattr(repo, "buscar_usuario", lambda db, id_usuario: estado.usuario)
    monkeypatch.setattr(repo, "modelo_eh_admin", lambda db, id_modelo: id_modelo in estado.admins)
    monkeypatch.setattr(repo, "codigos_do_modelo", lambda db, id_modelo: estado.modelos.get(id_modelo, set()))
    monkeypatch.setattr(repo, "excecoes_do_usuario", lambda db, id_usuario: estado.excecoes)
    return estado


@pytest.fixture
def client(banco):
    # app mínimo só com as dependências, para não depender das rotas do app.py
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: str(USER_ID)
    app.dependency_overrides[get_db] = lambda: None

    @app.get("/conta")
    def conta(usuario=Depends(permissoes.get_usuario_ativo)):
        return {"id": str(usuario.id_usuario)}

    @app.get("/estoque")
    def estoque(usuario=Depends(permissoes.exige_permissao("movimentar_estoque"))):
        return {"ok": True}

    return TestClient(app)


def test_conta_ativa_passa(client):
    resposta = client.get("/conta")

    assert resposta.status_code == 200
    assert resposta.json() == {"id": str(USER_ID)}


def test_usuario_sem_linha_no_banco_retorna_403(client, banco):
    banco.usuario = None

    resposta = client.get("/conta")

    assert resposta.status_code == 403
    assert resposta.json()["detail"] == "Usuário não cadastrado"


@pytest.mark.parametrize("status", ["pendente_ativacao", "inativa"])
def test_conta_nao_ativa_retorna_403(client, banco, status):
    banco.usuario = fazer_usuario(status_conta=status)

    resposta = client.get("/conta")

    assert resposta.status_code == 403
    assert resposta.json()["detail"] == "Conta não está ativa"


def test_id_do_token_malformado_retorna_401(client):
    client.app.dependency_overrides[get_current_user] = lambda: "nao-e-uuid"

    resposta = client.get("/conta")

    assert resposta.status_code == 401


def test_com_permissao_do_modelo_passa(client):
    assert client.get("/estoque").status_code == 200


def test_sem_permissao_retorna_403(client, banco):
    banco.modelos[MODELO_ESTOQUISTA] = {"atender_chamado"}

    resposta = client.get("/estoque")

    assert resposta.status_code == 403
    assert resposta.json()["detail"] == "Sem permissão"


def test_excecao_retirar_bloqueia(client, banco):
    banco.excecoes = {"movimentar_estoque": "retirar"}

    assert client.get("/estoque").status_code == 403


def test_excecao_acrescentar_libera(client, banco):
    banco.modelos[MODELO_ESTOQUISTA] = set()
    banco.excecoes = {"movimentar_estoque": "acrescentar"}

    assert client.get("/estoque").status_code == 200


def test_admin_passa_mesmo_com_excecao_retirar(client, banco):
    banco.usuario = fazer_usuario(id_modelo_acesso=MODELO_ADMIN)
    banco.excecoes = {"movimentar_estoque": "retirar"}

    assert client.get("/estoque").status_code == 200


def test_cliente_nao_tem_permissao_interna(client, banco):
    banco.usuario = fazer_usuario(tipo_conta="cliente", id_modelo_acesso=None)

    assert client.get("/estoque").status_code == 403


def test_conta_inativa_nao_chega_a_checar_permissao(client, banco):
    banco.usuario = fazer_usuario(status_conta="inativa", id_modelo_acesso=MODELO_ADMIN)

    resposta = client.get("/estoque")

    assert resposta.status_code == 403
    assert resposta.json()["detail"] == "Conta não está ativa"
