import uuid

import pytest

from src.use_cases.criar_admin import ErroCriarAdmin, criar_admin

ID_LOGIN = uuid.UUID("22222222-2222-2222-2222-222222222222")
ID_MODELO_ADMIN = 2


class SessaoFalsa:
    # responde às consultas do use case, na ordem: id do modelo Admin, e-mail já usado
    # em USUARIO, login já existente no Supabase Auth e, se existir, se ele está confirmado
    def __init__(self, id_modelo_admin=ID_MODELO_ADMIN, email_existente=None, login_existente=None,
                 login_confirmado=True, erro_commit=None):
        self.respostas = [id_modelo_admin, email_existente, login_existente, login_confirmado]
        self.erro_commit = erro_commit
        self.adicionados = []
        self.commits = 0
        self.rollbacks = 0

    def scalar(self, consulta, parametros=None):
        return self.respostas.pop(0)

    def add(self, objeto):
        self.adicionados.append(objeto)

    def commit(self):
        if self.erro_commit:
            raise self.erro_commit
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class AuthFalso:
    def __init__(self):
        self.criados = []
        self.apagados = []
        self.confirmados = []

    def criar_login(self, email, senha):
        self.criados.append(email)
        return ID_LOGIN

    def confirmar_email(self, id_usuario):
        self.confirmados.append(id_usuario)

    def apagar_login(self, id_usuario):
        self.apagados.append(id_usuario)


def test_cria_login_e_usuario_no_modelo_admin():
    db, auth = SessaoFalsa(), AuthFalso()

    usuario = criar_admin(db, auth, " Ana ", " Ana@Lorenzi.com ", "senha-forte")

    assert auth.criados == ["ana@lorenzi.com"]
    assert db.commits == 1
    assert usuario.id_usuario == ID_LOGIN
    assert usuario.nome == "Ana"
    assert usuario.email == "ana@lorenzi.com"
    assert usuario.tipo_conta == "interna"
    assert usuario.status_conta == "ativa"
    assert usuario.id_modelo_acesso == ID_MODELO_ADMIN


def test_sem_modelo_admin_nao_cria_login():
    db, auth = SessaoFalsa(id_modelo_admin=None), AuthFalso()

    with pytest.raises(ErroCriarAdmin, match="Modelo Admin não existe"):
        criar_admin(db, auth, "Ana", "ana@lorenzi.com", "senha-forte")

    assert auth.criados == []


def test_email_ja_usado_nao_cria_login():
    db, auth = SessaoFalsa(email_existente=uuid.uuid4()), AuthFalso()

    with pytest.raises(ErroCriarAdmin, match="Já existe usuário"):
        criar_admin(db, auth, "Ana", "ana@lorenzi.com", "senha-forte")

    assert auth.criados == []


def test_falha_ao_gravar_usuario_apaga_o_login():
    db, auth = SessaoFalsa(erro_commit=RuntimeError("banco caiu")), AuthFalso()

    with pytest.raises(RuntimeError, match="banco caiu"):
        criar_admin(db, auth, "Ana", "ana@lorenzi.com", "senha-forte")

    assert db.rollbacks == 1
    assert auth.apagados == [ID_LOGIN]


def test_login_ja_existente_e_reaproveitado_sem_senha():
    id_existente = uuid.uuid4()
    db, auth = SessaoFalsa(login_existente=id_existente), AuthFalso()

    usuario = criar_admin(db, auth, "Ana", "ana@lorenzi.com")

    assert auth.criados == []
    assert usuario.id_usuario == id_existente
    assert db.commits == 1


def test_falha_ao_gravar_nao_apaga_login_que_ja_existia():
    db = SessaoFalsa(login_existente=uuid.uuid4(), erro_commit=RuntimeError("banco caiu"))
    auth = AuthFalso()

    with pytest.raises(RuntimeError):
        criar_admin(db, auth, "Ana", "ana@lorenzi.com")

    assert auth.apagados == []


def test_login_novo_sem_senha_e_recusado():
    db, auth = SessaoFalsa(), AuthFalso()

    with pytest.raises(ErroCriarAdmin, match="Senha obrigatória"):
        criar_admin(db, auth, "Ana", "ana@lorenzi.com")

    assert auth.criados == []


def test_login_existente_nao_confirmado_e_confirmado():
    id_existente = uuid.uuid4()
    db, auth = SessaoFalsa(login_existente=id_existente, login_confirmado=False), AuthFalso()

    criar_admin(db, auth, "Ana", "ana@lorenzi.com")

    assert auth.confirmados == [id_existente]


def test_login_existente_ja_confirmado_nao_chama_a_api():
    db, auth = SessaoFalsa(login_existente=uuid.uuid4()), AuthFalso()

    criar_admin(db, auth, "Ana", "ana@lorenzi.com")

    assert auth.confirmados == []
