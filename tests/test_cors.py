from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.middlewares.cors import configurar_cors

FRONTEND = "https://grupo13-frontend.vercel.app"


def criar_client(origens=(FRONTEND,), origem_regex=None):
    app = FastAPI()
    configurar_cors(app, origens=list(origens), origem_regex=origem_regex)

    @app.get("/rota")
    def rota():
        return {"ok": True}

    return TestClient(app)


def preflight(client, origem):
    # requisição que o navegador faz antes de mandar o header Authorization para outro domínio
    return client.options(
        "/rota",
        headers={
            "Origin": origem,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )


def test_preflight_de_origem_liberada_e_aceito():
    resposta = preflight(criar_client(), FRONTEND)

    assert resposta.status_code == 200
    assert resposta.headers["access-control-allow-origin"] == FRONTEND
    assert "authorization" in resposta.headers["access-control-allow-headers"].lower()


def test_preflight_de_origem_desconhecida_e_recusado():
    resposta = preflight(criar_client(), "https://site-qualquer.com")

    assert resposta.status_code == 400
    assert "access-control-allow-origin" not in resposta.headers


def test_get_de_origem_liberada_devolve_header_cors():
    resposta = criar_client().get("/rota", headers={"Origin": FRONTEND})

    assert resposta.status_code == 200
    assert resposta.headers["access-control-allow-origin"] == FRONTEND


def test_regex_libera_previews_da_vercel():
    client = criar_client(origens=(), origem_regex=r"https://grupo13-frontend-.*\.vercel\.app")
    preview = "https://grupo13-frontend-abc123-nicolels1.vercel.app"

    assert preflight(client, preview).headers["access-control-allow-origin"] == preview
    assert "access-control-allow-origin" not in preflight(client, "https://outro.vercel.app").headers
