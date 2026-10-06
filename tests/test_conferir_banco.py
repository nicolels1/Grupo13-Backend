import pytest
from sqlalchemy.exc import DBAPIError

from scripts import conferir_banco


class Savepoint:
    def __init__(self, registro):
        self.registro = registro

    def commit(self):
        self.registro.append("commit")

    def rollback(self):
        self.registro.append("rollback")


class SessaoFalsa:
    # recusa todo SQL que contém "RECUSA", como o banco faria com uma permissão negada
    def __init__(self):
        self.savepoints = []

    def begin_nested(self):
        return Savepoint(self.savepoints)

    def execute(self, sql, parametros=None):
        if "RECUSA" in str(sql):
            raise DBAPIError(str(sql), parametros, Exception("permission denied"))


def test_tentar_sempre_desfaz_o_que_testou():
    db = SessaoFalsa()

    assert conferir_banco.tentar(db, "SELECT 1") is None
    assert isinstance(conferir_banco.tentar(db, "RECUSA"), DBAPIError)
    assert db.savepoints == ["rollback", "rollback"]


def test_tentar_e_manter_so_mantem_o_que_deu_certo():
    db = SessaoFalsa()

    assert conferir_banco.tentar_e_manter(db, "INSERT ...", {}) is None
    assert conferir_banco.tentar_e_manter(db, "RECUSA", {}) is not None
    assert db.savepoints == ["commit", "rollback"]


@pytest.mark.parametrize("sql, falhas", [("RECUSA", 0), ("DELETE aceito", 1)])
def test_deve_recusar_conta_falha_quando_o_banco_aceita(sql, falhas, capsys):
    conferencia = conferir_banco.Conferencia()

    conferir_banco.deve_recusar(conferencia, SessaoFalsa(), "apagar é recusado", sql)

    assert conferencia.falhas == falhas
    assert ("❌" in capsys.readouterr().out) == bool(falhas)


def test_relatorio_mostra_o_detalhe_so_quando_falha(capsys):
    conferencia = conferir_banco.Conferencia()

    conferencia.registrar("passou", True, "não aparece")
    conferencia.registrar("falhou", False, "motivo")

    saida = capsys.readouterr().out
    assert "✅ passou\n" in saida
    assert "❌ falhou (motivo)" in saida
    assert conferencia.falhas == 1
