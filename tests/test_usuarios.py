import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from src.app import app
from src.models.contas import Usuario, UsuarioPermissaoExcecao
from src.repositories import permissao_repository, unidade_repository, usuario_repository
from src.routes.contas import get_supabase_admin
from src.use_cases import usuarios
from src.use_cases.erros import Conflito, RecursoNaoEncontrado, RegraDeNegocio, ServicoIndisponivel
from src.utils.supabase_admin import ErroSupabase
from tests.apoio import SessaoFalsa, api, funcionario  # noqa: F401 (api é fixture)

ID_LOGIN = uuid.UUID("77777777-7777-7777-7777-777777777777")
ID_PESSOA = uuid.UUID("88888888-8888-8888-8888-888888888888")
MODELO_ADMIN, MODELO_ESTOQUISTA, MODELO_INATIVO = 1, 2, 3


class Sessao(SessaoFalsa):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.apagados = []

    def delete(self, objeto):
        self.apagados.append(objeto)


class AuthFalso:
    def __init__(self, erro=None):
        self.erro = erro
        self.chamadas = []

    def _registrar(self, *chamada):
        self.chamadas.append(chamada)
        if self.erro:
            raise self.erro

    def criar_login(self, email, senha):
        self._registrar("criar_login", email)
        return ID_LOGIN

    def convidar(self, email):
        self._registrar("convidar", email)
        return ID_LOGIN

    def apagar_login(self, id_usuario):
        self.chamadas.append(("apagar_login", id_usuario))

    def bloquear_login(self, id_usuario):
        self._registrar("bloquear_login", id_usuario)

    def desbloquear_login(self, id_usuario):
        self._registrar("desbloquear_login", id_usuario)


def pessoa(tipo_conta="interna", status_conta="ativa", id_modelo_acesso=MODELO_ESTOQUISTA):
    return Usuario(id_usuario=ID_PESSOA, nome="Rafael", email="rafael@casalorenzi.example", tipo_conta=tipo_conta,
                   status_conta=status_conta, id_modelo_acesso=id_modelo_acesso)


@pytest.fixture
def banco(monkeypatch):
    estado = SimpleNamespace(
        usuarios={}, emails=set(), logins=set(), confirmados=set(), excecoes={},
        modelos={MODELO_ADMIN: SimpleNamespace(ativo=True, eh_admin=True),
                 MODELO_ESTOQUISTA: SimpleNamespace(ativo=True, eh_admin=False),
                 MODELO_INATIVO: SimpleNamespace(ativo=False, eh_admin=False)},
        unidades={20: SimpleNamespace(ativo=True), 30: SimpleNamespace(ativo=False)},
        permissoes={"movimentar_estoque": 5, "gerenciar_contas": 1},
    )
    m = monkeypatch.setattr
    # a conta que acabou de ser criada ainda está só na sessão
    m(permissao_repository, "buscar_usuario", lambda db, i: estado.usuarios.get(i) or next(
        (o for o in getattr(db, "adicionados", []) if isinstance(o, Usuario) and o.id_usuario == i), None))
    m(permissao_repository, "buscar_modelo", lambda db, i: estado.modelos.get(i))
    m(permissao_repository, "modelo_eh_admin", lambda db, i: estado.modelos[i].eh_admin)
    m(permissao_repository, "buscar_permissao", lambda db, c: (
        SimpleNamespace(id_permissao=estado.permissoes[c], codigo=c) if c in estado.permissoes else None))
    m(permissao_repository, "buscar_excecao", lambda db, u, p: estado.excecoes.get((u, p)))
    m(permissao_repository, "excecoes_do_usuario", lambda db, u: {})
    m(unidade_repository, "buscar_unidade", lambda db, i: estado.unidades.get(i))
    m(usuario_repository, "email_em_uso", lambda db, e: e in estado.emails)
    m(usuario_repository, "buscar_login", lambda db, e: ID_LOGIN if e in estado.logins else None)
    m(usuario_repository, "login_confirmado", lambda db, i: i in estado.confirmados)
    m(usuario_repository, "listar_usuarios", lambda db, limit, offset, **f: ([], 0))
    m(usuarios, "montar_perfil", lambda db, u: {
        "id_usuario": u.id_usuario, "nome": u.nome, "email": u.email, "tipo_conta": u.tipo_conta,
        "status_conta": u.status_conta, "id_unidade": u.id_unidade, "modelo_acesso": None, "permissoes": [],
    })
    return estado


def nova_conta(**campos):
    dados = dict(nome="Rafael", email="rafael@casalorenzi.example", id_modelo_acesso=MODELO_ESTOQUISTA,
                 id_unidade=20, senha_provisoria=None)
    dados.update(campos)
    return dados


# ---------- criar conta interna ----------

def test_com_senha_provisoria_a_conta_ja_nasce_com_login(banco):
    db, auth = Sessao(), AuthFalso()

    saida = usuarios.criar_conta_interna(db, auth, nova_conta(senha_provisoria="Provisoria#1"))

    assert auth.chamadas == [("criar_login", "rafael@casalorenzi.example")]
    criada = db.adicionados[0]
    assert (criada.id_usuario, criada.tipo_conta, criada.status_conta) == (ID_LOGIN, "interna", "ativa")
    assert (criada.id_modelo_acesso, criada.id_unidade) == (MODELO_ESTOQUISTA, 20)
    assert saida["convite_pendente"] is True  # login falso não conta como confirmado
    assert db.commits == 1


def test_sem_senha_vai_convite_por_email(banco):
    auth = AuthFalso()

    usuarios.criar_conta_interna(Sessao(), auth, nova_conta())

    assert auth.chamadas == [("convidar", "rafael@casalorenzi.example")]


@pytest.mark.parametrize("onde", ["emails", "logins"])
def test_email_ja_usado_nao_cria_login(banco, onde):
    getattr(banco, onde).add("rafael@casalorenzi.example")
    auth = AuthFalso()

    with pytest.raises(Conflito, match="E-mail já cadastrado"):
        usuarios.criar_conta_interna(Sessao(), auth, nova_conta())

    assert auth.chamadas == []


@pytest.mark.parametrize(
    "campos, erro, mensagem",
    [
        (dict(id_modelo_acesso=99), RecursoNaoEncontrado, "Modelo"),
        (dict(id_modelo_acesso=MODELO_INATIVO), RegraDeNegocio, "Modelo de acesso desativado"),
        (dict(id_unidade=99), RecursoNaoEncontrado, "Unidade"),
        (dict(id_unidade=30), RegraDeNegocio, "Unidade desativada"),
    ],
)
def test_modelo_e_unidade_precisam_existir_e_estar_ativos(banco, campos, erro, mensagem):
    with pytest.raises(erro, match=mensagem):
        usuarios.criar_conta_interna(Sessao(), AuthFalso(), nova_conta(**campos))


@pytest.mark.parametrize(
    "erro, classe",
    [
        (ErroSupabase(400, "email_address_invalid", "invalid"), RegraDeNegocio),
        (ErroSupabase(500, "unexpected_failure", "Error sending invite email"), ServicoIndisponivel),
    ],
)
def test_convite_que_nao_sai_sugere_senha_provisoria(banco, erro, classe):
    with pytest.raises(classe, match="senha provisória"):
        usuarios.criar_conta_interna(Sessao(), AuthFalso(erro=erro), nova_conta())


def test_falha_ao_gravar_apaga_o_login_criado(banco):
    db, auth = Sessao(erro_commit=RuntimeError("banco caiu")), AuthFalso()

    with pytest.raises(RuntimeError):
        usuarios.criar_conta_interna(db, auth, nova_conta(senha_provisoria="Provisoria#1"))

    assert ("apagar_login", ID_LOGIN) in auth.chamadas


# ---------- alterar ----------

def test_desativar_bloqueia_o_login_depois_do_banco(banco):
    banco.usuarios[ID_PESSOA] = pessoa()
    db, auth = Sessao(), AuthFalso()

    usuarios.alterar(db, auth, ID_PESSOA, {"status_conta": "inativa"})

    assert banco.usuarios[ID_PESSOA].status_conta == "inativa"
    assert auth.chamadas == [("bloquear_login", ID_PESSOA)]
    assert db.commits == 1


def test_reativar_desbloqueia_o_login(banco):
    banco.usuarios[ID_PESSOA] = pessoa(status_conta="inativa")
    auth = AuthFalso()

    usuarios.alterar(Sessao(), auth, ID_PESSOA, {"status_conta": "ativa"})

    assert auth.chamadas == [("desbloquear_login", ID_PESSOA)]


def test_se_o_supabase_falha_o_status_volta(banco):
    banco.usuarios[ID_PESSOA] = pessoa()
    db = Sessao()

    with pytest.raises(ServicoIndisponivel):
        usuarios.alterar(db, AuthFalso(erro=ErroSupabase(503, "indisponivel", "x")), ID_PESSOA,
                         {"status_conta": "inativa"})

    assert banco.usuarios[ID_PESSOA].status_conta == "ativa"
    assert db.commits == 2


def test_mudar_nome_nao_chama_o_supabase(banco):
    banco.usuarios[ID_PESSOA] = pessoa()
    auth = AuthFalso()

    usuarios.alterar(Sessao(), auth, ID_PESSOA, {"nome": "Rafael Souza"})

    assert banco.usuarios[ID_PESSOA].nome == "Rafael Souza"
    assert auth.chamadas == []


def test_cliente_nao_recebe_modelo_nem_unidade(banco):
    banco.usuarios[ID_PESSOA] = pessoa(tipo_conta="cliente", id_modelo_acesso=None)

    with pytest.raises(RegraDeNegocio, match="só de conta interna"):
        usuarios.alterar(Sessao(), AuthFalso(), ID_PESSOA, {"id_unidade": 20})


def test_trocar_para_modelo_desativado_e_recusado(banco):
    banco.usuarios[ID_PESSOA] = pessoa()

    with pytest.raises(RegraDeNegocio, match="desativado"):
        usuarios.alterar(Sessao(), AuthFalso(), ID_PESSOA, {"id_modelo_acesso": MODELO_INATIVO})


# ---------- reenviar convite ----------

def test_reenvia_convite_de_quem_ainda_nao_definiu_senha(banco):
    banco.usuarios[ID_PESSOA] = pessoa()
    auth = AuthFalso()

    usuarios.reenviar_convite(Sessao(), auth, ID_PESSOA)

    assert auth.chamadas == [("convidar", "rafael@casalorenzi.example")]


def test_nao_reenvia_para_quem_ja_tem_senha(banco):
    banco.usuarios[ID_PESSOA] = pessoa()
    banco.confirmados.add(ID_PESSOA)

    with pytest.raises(RegraDeNegocio, match="já definiu a senha"):
        usuarios.reenviar_convite(Sessao(), AuthFalso(), ID_PESSOA)


# ---------- exceções ----------

def test_cria_excecao_nova(banco):
    banco.usuarios[ID_PESSOA] = pessoa()
    db = Sessao()

    usuarios.definir_excecao(db, ID_PESSOA, "movimentar_estoque", "retirar")

    criada = db.adicionados[0]
    assert isinstance(criada, UsuarioPermissaoExcecao)
    assert (criada.id_usuario, criada.id_permissao, criada.efeito) == (ID_PESSOA, 5, "retirar")


def test_troca_o_efeito_de_excecao_existente(banco):
    banco.usuarios[ID_PESSOA] = pessoa()
    existente = SimpleNamespace(efeito="acrescentar")
    banco.excecoes[(ID_PESSOA, 5)] = existente
    db = Sessao()

    usuarios.definir_excecao(db, ID_PESSOA, "movimentar_estoque", "retirar")

    assert existente.efeito == "retirar"
    assert db.adicionados == []


@pytest.mark.parametrize(
    "conta, codigo, efeito, mensagem",
    [
        (dict(tipo_conta="cliente", id_modelo_acesso=None), "movimentar_estoque", "acrescentar", "conta interna"),
        (dict(), "gerenciar_contas", "acrescentar", "Gestão"),
        (dict(id_modelo_acesso=MODELO_ADMIN), "movimentar_estoque", "retirar", "Admin não aceita"),
    ],
)
def test_regras_das_excecoes(banco, conta, codigo, efeito, mensagem):
    banco.usuarios[ID_PESSOA] = pessoa(**conta)

    with pytest.raises(RegraDeNegocio, match=mensagem):
        usuarios.definir_excecao(Sessao(), ID_PESSOA, codigo, efeito)


def test_admin_aceita_excecao_de_acrescentar(banco):
    banco.usuarios[ID_PESSOA] = pessoa(id_modelo_acesso=MODELO_ADMIN)

    usuarios.definir_excecao(Sessao(), ID_PESSOA, "movimentar_estoque", "acrescentar")


def test_permissao_inexistente(banco):
    banco.usuarios[ID_PESSOA] = pessoa()

    with pytest.raises(RecursoNaoEncontrado, match="Permissão"):
        usuarios.definir_excecao(Sessao(), ID_PESSOA, "voar", "acrescentar")


def test_remover_excecao(banco):
    banco.usuarios[ID_PESSOA] = pessoa()
    existente = SimpleNamespace(efeito="retirar")
    banco.excecoes[(ID_PESSOA, 5)] = existente
    db = Sessao()

    usuarios.remover_excecao(db, ID_PESSOA, "movimentar_estoque")

    assert db.apagados == [existente]
    assert db.commits == 1


def test_remover_excecao_que_nao_existe(banco):
    banco.usuarios[ID_PESSOA] = pessoa()

    with pytest.raises(RecursoNaoEncontrado, match="não tem exceção"):
        usuarios.remover_excecao(Sessao(), ID_PESSOA, "movimentar_estoque")


# ---------- consulta ----------

def test_lista_de_contas_busca_por_nome_ou_email():
    texto = str(usuario_repository.consulta_usuarios(busca="ana").compile(dialect=postgresql.dialect()))
    assert "usuario.nome ILIKE" in texto and "usuario.email ILIKE" in texto
    assert "LEFT OUTER JOIN modelo_acesso" in texto  # cliente não tem modelo e continua na lista


# ---------- rotas ----------

@pytest.fixture
def admin_api(api, banco):
    # login de Admin e Supabase falso: sem isso a rota tentaria ler a chave real do .env (que o CI não tem)
    def montar(permitido=True, auth=None):
        client = api(Sessao(), usuario=funcionario(), permitido=permitido)
        app.dependency_overrides[get_supabase_admin] = lambda: auth or AuthFalso()
        return client
    return montar


def test_gestao_de_contas_exige_gerenciar_contas(admin_api):
    assert admin_api(permitido=False).get("/usuarios").status_code == 403


def test_criar_conta_pela_api(admin_api):
    auth = AuthFalso()

    resposta = admin_api(auth=auth).post("/usuarios", json={
        "nome": "Rafael", "email": "Rafael@CasaLorenzi.example", "id_modelo_acesso": MODELO_ESTOQUISTA,
        "senha_provisoria": "Provisoria#1",
    })

    assert resposta.status_code == 201
    assert resposta.json()["email"] == "rafael@casalorenzi.example"
    assert auth.chamadas == [("criar_login", "rafael@casalorenzi.example")]


@pytest.mark.parametrize(
    "corpo",
    [
        {"nome": "Rafael", "email": "rafael@x.com", "id_modelo_acesso": 2, "senha_provisoria": "123"},
        {"nome": "Rafael", "email": "sem-arroba", "id_modelo_acesso": 2},
    ],
)
def test_dados_invalidos_na_criacao(admin_api, corpo):
    assert admin_api().post("/usuarios", json=corpo).status_code == 422


def test_status_fora_da_lista(admin_api):
    resposta = admin_api().patch(f"/usuarios/{ID_PESSOA}", json={"status_conta": "pendente_ativacao"})

    assert resposta.status_code == 422
