import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from src.middlewares import auth

USER_ID = "11111111-1111-1111-1111-111111111111"

# par de chaves gerado só para os testes, no lugar da chave do Supabase
CHAVE_PRIVADA = ec.generate_private_key(ec.SECP256R1())
OUTRA_CHAVE_PRIVADA = ec.generate_private_key(ec.SECP256R1())


def gerar_token(chave=CHAVE_PRIVADA, **campos):
    payload = {"sub": USER_ID, "aud": "authenticated", "exp": int(time.time()) + 300}
    payload.update(campos)
    return jwt.encode(payload, chave, algorithm="ES256")


@pytest.fixture
def client(monkeypatch):
    # em vez de buscar a chave pública no Supabase, devolve a chave de teste
    monkeypatch.setattr(
        auth.jwks_client,
        "get_signing_key_from_jwt",
        lambda token: SimpleNamespace(key=CHAVE_PRIVADA.public_key()),
    )

    # app mínimo só com a dependência, para não depender das rotas do app.py
    app = FastAPI()

    @app.get("/rota-protegida")
    def rota_protegida(user_id: str = Depends(auth.get_current_user)):
        return {"user_id": user_id}

    return TestClient(app)


def test_sem_header_retorna_401(client):
    resposta = client.get("/rota-protegida")

    assert resposta.status_code == 401
    assert resposta.json()["detail"] == "Token ausente"


def test_header_sem_bearer_retorna_401(client):
    resposta = client.get("/rota-protegida", headers={"Authorization": gerar_token()})

    assert resposta.status_code == 401
    assert resposta.json()["detail"] == "Token ausente"


def test_token_valido_retorna_id_do_usuario(client):
    resposta = client.get(
        "/rota-protegida", headers={"Authorization": f"Bearer {gerar_token()}"}
    )

    assert resposta.status_code == 200
    assert resposta.json() == {"user_id": USER_ID}


@pytest.mark.parametrize(
    "token",
    [
        pytest.param("nao-e-um-jwt", id="malformado"),
        pytest.param(gerar_token(exp=int(time.time()) - 60), id="expirado"),
        pytest.param(gerar_token(aud="outro"), id="audience-errada"),
        pytest.param(gerar_token(chave=OUTRA_CHAVE_PRIVADA), id="assinatura-de-outra-chave"),
    ],
)
def test_token_invalido_retorna_401(client, token):
    resposta = client.get("/rota-protegida", headers={"Authorization": f"Bearer {token}"})

    assert resposta.status_code == 401
    assert resposta.json()["detail"] == "Token inválido"


def falhar_busca_da_chave(monkeypatch, erro):
    def buscar(token):
        raise erro

    monkeypatch.setattr(auth.jwks_client, "get_signing_key_from_jwt", buscar)


def test_chave_desconhecida_pelo_supabase_retorna_401(client, monkeypatch):
    # ex.: token de outro projeto Supabase; antes do ajuste isso virava erro 500
    falhar_busca_da_chave(monkeypatch, jwt.PyJWKClientError("Unable to find a signing key"))

    resposta = client.get("/rota-protegida", headers={"Authorization": f"Bearer {gerar_token()}"})

    assert resposta.status_code == 401
    assert resposta.json()["detail"] == "Token inválido"


def test_supabase_fora_do_ar_retorna_503(client, monkeypatch):
    falhar_busca_da_chave(monkeypatch, jwt.PyJWKClientConnectionError("Fail to fetch data"))

    resposta = client.get("/rota-protegida", headers={"Authorization": f"Bearer {gerar_token()}"})

    assert resposta.status_code == 503
    assert resposta.json()["detail"] == "Serviço de autenticação indisponível"
