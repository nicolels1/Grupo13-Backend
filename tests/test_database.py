import importlib
import sys

import pytest
from sqlalchemy.orm import Session

from src.config import settings


def importar_session(monkeypatch, database_url):
    # session.py lê a URL ao ser importado, então o módulo é reimportado a cada teste
    monkeypatch.setattr(settings, "DATABASE_URL", database_url)
    monkeypatch.delitem(sys.modules, "src.database.session", raising=False)
    return importlib.import_module("src.database.session")


def test_sem_database_url_da_erro_claro(monkeypatch):
    with pytest.raises(RuntimeError, match="DATABASE_URL não definida"):
        importar_session(monkeypatch, None)


def test_engine_usa_driver_psycopg(monkeypatch):
    # create_engine não conecta no banco, então a URL pode ser fictícia
    session = importar_session(monkeypatch, "postgresql://usuario:senha@localhost:5432/teste")

    assert session.engine.dialect.driver == "psycopg"


def test_get_db_entrega_sessao_e_fecha(monkeypatch):
    session = importar_session(monkeypatch, "postgresql://usuario:senha@localhost:5432/teste")
    fechadas = []
    monkeypatch.setattr(Session, "close", lambda self: fechadas.append(self))

    gerador = session.get_db()
    db = next(gerador)
    assert isinstance(db, Session)

    gerador.close()  # o FastAPI faz isso ao terminar a requisição
    assert fechadas == [db]
