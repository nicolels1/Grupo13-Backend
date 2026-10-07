import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.app import app
from src.database.session import get_db
from src.middlewares.permissoes import get_usuario_ativo
from src.repositories import pedido_repository, permissao_repository, usuario_repository
from src.routes.contas import get_supabase_admin, get_supabase_login
from src.use_cases import contas
from src.use_cases.erros import (
    Conflito, MuitasTentativas, NaoAutenticado, RegraDeNegocio, SemPermissao, ServicoIndisponivel,
)
from src.use_cases.permissoes import permissoes_efetivas
from src.utils.supabase_admin import ErroSupabase

ID_LOGIN = uuid.UUID("22222222-2222-2222-2222-222222222222")
CPF = "52998224725"


class SessaoFalsa:
    def __init__(self, erro_commit=None):
        self.erro_commit = erro_commit
        self.adicionados = []
        self.commits = 0
        self.rollbacks = 0

    def add(self, objeto):
        self.adicionados.append(objeto)

    def flush(self):
        pass

    def commit(self):
        if self.erro_commit:
            raise self.erro_commit
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class AuthFalso:
    def __init__(self, erro_criar=None, erro_apagar=None, erro_login=None):
        self.erro_criar = erro_criar
        self.erro_apagar = erro_apagar
        self.erro_login = erro_login
        self.criados = []
        self.apagados = []
        self.logins = []

    def criar_login(self, email, senha):
        if self.erro_criar:
            raise self.erro_criar
        self.criados.append(email)
        return ID_LOGIN

    def apagar_login(self, id_usuario):
        self.apagados.append(id_usuario)
        if self.erro_apagar:
            raise self.erro_apagar

    def entrar_com_senha(self, email, senha):
        self.logins.append(email)
        if self.erro_login:
            raise self.erro_login
        return {"access_token": "acesso", "refresh_token": "renova", "expires_in": 3600, "token_type": "bearer"}


@pytest.fixture
def banco(monkeypatch):
    # estado do "banco" usado pelas consultas dos repositories, trocadas por versões falsas
    estado = SimpleNamespace(emails=set(), cpfs=set(), clientes={}, compras_da_loja={CPF: 2}, ligadas=[])

    def ligar(db, cpf, id_cliente):
        estado.ligadas.append((cpf, id_cliente))
        return estado.compras_da_loja.get(cpf, 0)

    monkeypatch.setattr(pedido_repository, "ligar_pedidos_pelo_cpf", ligar)
    monkeypatch.setattr(usuario_repository, "email_em_uso", lambda db, email: email in estado.emails)
    monkeypatch.setattr(usuario_repository, "cpf_em_uso", lambda db, cpf: cpf in estado.cpfs)
    monkeypatch.setattr(usuario_repository, "buscar_cliente_por_cpf", lambda db, cpf: estado.clientes.get(cpf))
    return estado


def cadastrar(db, auth):
    return contas.cadastrar_cliente(db, auth, "Ana", "ana@email.com", CPF, "senha-forte")


# ---------- cadastro de cliente ----------

def test_cadastro_cria_login_e_cliente_ativo(banco):
    db, auth = SessaoFalsa(), AuthFalso()

    resposta = cadastrar(db, auth)

    assert auth.criados == ["ana@email.com"]
    assert db.commits == 1
    [usuario] = db.adicionados
    assert (usuario.id_usuario, usuario.cpf) == (ID_LOGIN, CPF)
    assert (resposta["tipo_conta"], resposta["status_conta"]) == ("cliente", "ativa")
    assert usuario.id_modelo_acesso is None


def test_cadastro_liga_as_compras_da_loja_com_o_cpf_na_nota(banco):
    resposta = cadastrar(SessaoFalsa(), AuthFalso())

    assert banco.ligadas == [(CPF, ID_LOGIN)]
    assert resposta["compras_ligadas"] == 2


@pytest.mark.parametrize("campo, mensagem", [("emails", "E-mail já cadastrado"), ("cpfs", "CPF já cadastrado")])
def test_email_ou_cpf_repetido_nao_cria_login(banco, campo, mensagem):
    getattr(banco, campo).update({"ana@email.com", CPF})
    auth = AuthFalso()

    with pytest.raises(Conflito, match=mensagem):
        cadastrar(SessaoFalsa(), auth)

    assert auth.criados == []


@pytest.mark.parametrize(
    "erro, classe",
    [
        (ErroSupabase(422, "email_exists", "x"), Conflito),
        (ErroSupabase(422, "weak_password", "x"), RegraDeNegocio),
        (ErroSupabase(429, "over_request_rate_limit", "x"), MuitasTentativas),
        (ErroSupabase(503, "indisponivel", "x"), ServicoIndisponivel),
        (ErroSupabase(400, "outro_erro", "x"), RegraDeNegocio),
    ],
)
def test_recusa_do_supabase_vira_erro_com_mensagem(banco, erro, classe):
    with pytest.raises(classe):
        cadastrar(SessaoFalsa(), AuthFalso(erro_criar=erro))


def test_falha_ao_gravar_cliente_apaga_o_login(banco):
    db, auth = SessaoFalsa(erro_commit=RuntimeError("banco caiu")), AuthFalso()

    with pytest.raises(RuntimeError, match="banco caiu"):
        cadastrar(db, auth)

    assert db.rollbacks == 1
    assert auth.apagados == [ID_LOGIN]


def test_falha_ao_apagar_login_mantem_o_erro_original(banco):
    db = SessaoFalsa(erro_commit=RuntimeError("banco caiu"))
    auth = AuthFalso(erro_apagar=ErroSupabase(503, "indisponivel", "x"))

    with pytest.raises(RuntimeError, match="banco caiu"):
        cadastrar(db, auth)


# ---------- login por CPF ----------

def cliente(status_conta="ativa"):
    return SimpleNamespace(email="ana@email.com", status_conta=status_conta)


def test_login_por_cpf_usa_o_email_do_cliente(banco):
    banco.clientes[CPF] = cliente()
    auth = AuthFalso()

    sessao = contas.entrar_com_cpf(None, auth, CPF, "senha-forte")

    assert auth.logins == ["ana@email.com"]
    assert sessao["access_token"] == "acesso"


def test_cpf_sem_cadastro_e_senha_errada_dao_a_mesma_resposta(banco):
    with pytest.raises(NaoAutenticado) as sem_cadastro:
        contas.entrar_com_cpf(None, AuthFalso(), CPF, "senha")

    banco.clientes[CPF] = cliente()
    erro = ErroSupabase(400, "invalid_credentials", "Invalid login credentials")
    with pytest.raises(NaoAutenticado) as senha_errada:
        contas.entrar_com_cpf(None, AuthFalso(erro_login=erro), CPF, "errada")

    assert sem_cadastro.value.mensagem == senha_errada.value.mensagem == "CPF ou senha incorretos"


def test_login_de_conta_inativa_e_recusado(banco):
    banco.clientes[CPF] = cliente(status_conta="inativa")

    with pytest.raises(SemPermissao, match="Conta não está ativa"):
        contas.entrar_com_cpf(None, AuthFalso(), CPF, "senha-forte")


def test_supabase_fora_do_ar_no_login(banco):
    banco.clientes[CPF] = cliente()

    with pytest.raises(ServicoIndisponivel):
        contas.entrar_com_cpf(None, AuthFalso(erro_login=ErroSupabase(503, "indisponivel", "x")), CPF, "s")


# ---------- permissões mostradas no /me ----------

def efetivas(**campos):
    padrao = dict(eh_admin=False, todas=set(), do_modelo=set(), acrescentadas=set(), retiradas=set())
    padrao.update(campos)
    return permissoes_efetivas(**padrao)


def test_admin_recebe_todas_as_permissoes_cadastradas():
    todas = {"movimentar_estoque", "gerenciar_contas"}
    assert efetivas(eh_admin=True, todas=todas, retiradas={"gerenciar_contas"}) == todas


def test_permissoes_do_modelo_com_excecoes():
    resultado = efetivas(
        do_modelo={"movimentar_estoque", "definir_estoque_minimo"},
        acrescentadas={"atender_chamado"},
        retiradas={"definir_estoque_minimo"},
    )
    assert resultado == {"movimentar_estoque", "atender_chamado"}


def test_gestao_nunca_aparece_fora_do_admin():
    assert efetivas(do_modelo={"gerenciar_unidades"}, acrescentadas={"gerenciar_contas"}) == set()


# ---------- rotas ----------

@pytest.fixture
def client(banco):
    db, auth = SessaoFalsa(), AuthFalso()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_supabase_admin] = lambda: auth
    app.dependency_overrides[get_supabase_login] = lambda: auth
    yield TestClient(app)
    app.dependency_overrides.clear()


CADASTRO = {"nome": " Ana ", "email": "Ana@Email.com", "cpf": "529.982.247-25", "senha": "senha-forte"}


def test_post_clientes_responde_201_sem_cpf_nem_senha(client):
    resposta = client.post("/clientes", json=CADASTRO)

    assert resposta.status_code == 201
    assert resposta.json() == {
        "id_usuario": str(ID_LOGIN),
        "nome": "Ana",
        "email": "ana@email.com",
        "tipo_conta": "cliente",
        "status_conta": "ativa",
        "compras_ligadas": 2,
    }


@pytest.mark.parametrize(
    "campo, valor",
    [("cpf", "529.982.247-24"), ("email", "sem-arroba"), ("senha", "123"), ("nome", " ")],
)
def test_post_clientes_com_dado_invalido_responde_422(client, campo, valor):
    resposta = client.post("/clientes", json={**CADASTRO, campo: valor})

    assert resposta.status_code == 422


def test_post_clientes_com_cpf_repetido_responde_409(client, banco):
    banco.cpfs.add(CPF)

    resposta = client.post("/clientes", json=CADASTRO)

    assert resposta.status_code == 409
    assert resposta.json() == {"detail": "CPF já cadastrado"}


def test_post_login_cpf_devolve_a_sessao(client, banco):
    banco.clientes[CPF] = cliente()

    resposta = client.post("/login/cpf", json={"cpf": "529.982.247-25", "senha": "senha-forte"})

    assert resposta.status_code == 200
    assert resposta.json() == {
        "access_token": "acesso", "refresh_token": "renova", "expires_in": 3600, "token_type": "bearer",
    }


def test_post_login_cpf_sem_cadastro_responde_401(client):
    resposta = client.post("/login/cpf", json={"cpf": "529.982.247-25", "senha": "senha"})

    assert resposta.status_code == 401
    assert resposta.json() == {"detail": "CPF ou senha incorretos"}


def usuario_logado(**campos):
    padrao = dict(
        id_usuario=ID_LOGIN, nome="Bia", email="bia@lorenzi.com", tipo_conta="interna",
        status_conta="ativa", id_modelo_acesso=2, id_unidade=1,
    )
    padrao.update(campos)
    return SimpleNamespace(**padrao)


def test_get_me_de_funcionario_traz_modelo_e_permissoes(client, monkeypatch):
    app.dependency_overrides[get_usuario_ativo] = lambda: usuario_logado()
    modelo = SimpleNamespace(id_modelo=2, nome="Estoquista", eh_admin=False)
    monkeypatch.setattr(permissao_repository, "buscar_modelo", lambda db, id_modelo: modelo)
    monkeypatch.setattr(permissao_repository, "codigos_do_modelo", lambda db, id_modelo: {"movimentar_estoque"})
    monkeypatch.setattr(permissao_repository, "excecoes_do_usuario", lambda db, id_usuario: {"atender_chamado": "acrescentar"})

    resposta = client.get("/me")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["tipo_conta"] == "interna"
    assert corpo["modelo_acesso"] == {"id_modelo": 2, "nome": "Estoquista", "eh_admin": False}
    assert corpo["permissoes"] == ["atender_chamado", "movimentar_estoque"]


def test_get_me_de_cliente_nao_tem_permissoes(client):
    app.dependency_overrides[get_usuario_ativo] = lambda: usuario_logado(
        tipo_conta="cliente", id_modelo_acesso=None, id_unidade=None
    )

    resposta = client.get("/me")

    assert resposta.status_code == 200
    assert resposta.json()["modelo_acesso"] is None
    assert resposta.json()["permissoes"] == []


def test_get_me_sem_token_responde_401(client):
    assert client.get("/me").status_code == 401
