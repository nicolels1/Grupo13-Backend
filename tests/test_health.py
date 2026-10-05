import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from src.app import app
from src.database.session import get_db


class SessaoFalsa:
    def __init__(self, erro=None):
        self.erro = erro
        self.consultas = []

    def execute(self, consulta):
        if self.erro:
            raise self.erro
        self.consultas.append(str(consulta))


@pytest.fixture
def usar_sessao():
    # troca a sessão real do banco por uma falsa durante o teste
    def trocar(sessao):
        app.dependency_overrides[get_db] = lambda: sessao
        return TestClient(app)

    yield trocar
    app.dependency_overrides.clear()


def test_health_com_banco_ok(usar_sessao):
    sessao = SessaoFalsa()

    resposta = usar_sessao(sessao).get("/health")

    assert resposta.status_code == 200
    assert resposta.json() == {"status": "ok"}
    assert sessao.consultas == ["SELECT 1"]


def test_health_com_banco_fora_retorna_503(usar_sessao):
    erro = OperationalError("SELECT 1", {}, Exception("conexão recusada"))

    resposta = usar_sessao(SessaoFalsa(erro=erro)).get("/health")

    assert resposta.status_code == 503
    assert resposta.json()["detail"] == "Banco indisponível"
