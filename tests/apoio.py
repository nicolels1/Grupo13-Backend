import itertools
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.app import app
from src.database.session import get_db
from src.middlewares.permissoes import get_usuario_ativo
from src.models.estoque import MovimentacaoEstoque
from src.repositories import permissao_repository

ID_FUNCIONARIO = uuid.UUID("33333333-3333-3333-3333-333333333333")


class SessaoFalsa:
    """Sessão do SQLAlchemy de mentira: guarda o que foi adicionado e, no refresh,
    preenche o id e os valores padrão das colunas como o banco faria."""

    def __init__(self, erro_commit=None, primeiro_id=1):
        self.erro_commit = erro_commit
        self.adicionados = []
        self.apagados = []
        self.commits = 0
        self.rollbacks = 0
        self._ids = itertools.count(primeiro_id)

    def add(self, objeto):
        self.adicionados.append(objeto)

    def add_all(self, objetos):
        self.adicionados.extend(objetos)

    def delete(self, objeto):
        self.apagados.append(objeto)

    def flush(self):
        for objeto in self.adicionados:
            self.refresh(objeto)

    def commit(self):
        if self.erro_commit:
            raise self.erro_commit
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def refresh(self, objeto):
        tabela = objeto.__table__
        for coluna in tabela.primary_key:
            if getattr(objeto, coluna.key) is None and coluna.autoincrement in (True, "auto"):
                setattr(objeto, coluna.key, next(self._ids))
        for coluna in tabela.columns:
            if getattr(objeto, coluna.key) is None and coluna.default is not None and coluna.default.is_scalar:
                setattr(objeto, coluna.key, coluna.default.arg)


class SessaoComTrigger(SessaoFalsa):
    """Faz o papel do trigger do banco: cada movimentação no flush muda o saldo."""

    def __init__(self, saldos):
        super().__init__()
        self.saldos = saldos
        self.aplicadas = set()

    def flush(self):
        super().flush()
        for objeto in self.adicionados:
            if isinstance(objeto, MovimentacaoEstoque) and id(objeto) not in self.aplicadas:
                self.aplicadas.add(id(objeto))
                chave = (objeto.id_variante, objeto.id_unidade, objeto.canal)
                linha = self.saldos.setdefault(chave, SimpleNamespace(quantidade=0, quantidade_reservada=0))
                linha.quantidade += objeto.quantidade


def funcionario(**campos):
    dados = dict(id_usuario=ID_FUNCIONARIO, tipo_conta="interna", status_conta="ativa", id_modelo_acesso=1, id_unidade=None)
    dados.update(campos)
    return SimpleNamespace(**dados)


@pytest.fixture
def api(monkeypatch):
    """Cliente da API com banco falso e login simulado.

    api(db) → sem login; api(db, usuario=funcionario()) → logado com todas as permissões;
    api(db, usuario=..., permitido=False) → logado, sem nenhuma permissão."""

    def montar(db, usuario=None, permitido=True):
        app.dependency_overrides[get_db] = lambda: db
        if usuario is not None:
            app.dependency_overrides[get_usuario_ativo] = lambda: usuario
            monkeypatch.setattr(permissao_repository, "modelo_eh_admin", lambda db, id_modelo: permitido)
            monkeypatch.setattr(permissao_repository, "codigos_do_modelo", lambda db, id_modelo: set())
            monkeypatch.setattr(permissao_repository, "excecoes_do_usuario", lambda db, id_usuario: {})
        return TestClient(app)

    yield montar
    app.dependency_overrides.clear()


class StorageFalso:
    """Supabase Storage de mentira: guarda os arquivos enviados e apagados."""

    def __init__(self, erro=None):
        self.erro = erro
        self.enviados = {}
        self.apagados = []

    def enviar(self, bucket, caminho, conteudo, tipo):
        if self.erro:
            raise self.erro
        self.enviados[(bucket, caminho)] = (conteudo, tipo)

    def apagar(self, bucket, caminho):
        self.apagados.append((bucket, caminho))

    def url_temporaria(self, bucket, caminho, segundos=3600):
        return f"https://teste/{bucket}/{caminho}?token=x"


PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
PDF = b"%PDF-1.7" + b"0" * 32
