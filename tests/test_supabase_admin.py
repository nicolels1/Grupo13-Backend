import uuid

import httpx2
import pytest

from src.utils import supabase_admin
from src.utils.supabase_admin import ErroSupabase, SupabaseAdmin, SupabaseLogin

URL = "https://teste.supabase.co"


@pytest.fixture
def requisicoes(monkeypatch):
    # troca a chamada HTTP por uma resposta pronta e guarda o que foi pedido
    enviadas = []

    def responder_com(resposta):
        def request(metodo, url, **kwargs):
            enviadas.append((metodo, url, kwargs))
            if isinstance(resposta, Exception):
                raise resposta
            return resposta

        monkeypatch.setattr(supabase_admin.httpx2, "request", request)
        return enviadas

    return responder_com


def test_criar_login_nasce_confirmado(requisicoes):
    id_login = uuid.uuid4()
    enviadas = requisicoes(httpx2.Response(200, json={"id": str(id_login)}))

    resultado = SupabaseAdmin(URL, "chave").criar_login("ana@lorenzi.com", "senha-forte")

    metodo, url, kwargs = enviadas[0]
    assert resultado == id_login
    assert (metodo, url) == ("POST", f"{URL}/auth/v1/admin/users")
    assert kwargs["json"]["email_confirm"] is True


def test_erro_do_supabase_traz_status_codigo_e_mensagem(requisicoes):
    requisicoes(httpx2.Response(422, json={"code": 422, "error_code": "email_exists", "msg": "already registered"}))

    with pytest.raises(ErroSupabase) as erro:
        SupabaseAdmin(URL, "chave").criar_login("ana@lorenzi.com", "senha-forte")

    assert (erro.value.status, erro.value.codigo, erro.value.mensagem) == (422, "email_exists", "already registered")


def test_erro_no_formato_antigo_do_token(requisicoes):
    requisicoes(httpx2.Response(400, json={"error": "invalid_grant", "error_description": "Invalid login credentials"}))

    with pytest.raises(ErroSupabase) as erro:
        SupabaseLogin(URL, "chave-publica").entrar_com_senha("ana@lorenzi.com", "errada")

    assert (erro.value.status, erro.value.codigo) == (400, "invalid_grant")


def test_supabase_sem_resposta_vira_503(requisicoes):
    requisicoes(httpx2.ConnectError("conexão recusada"))

    with pytest.raises(ErroSupabase) as erro:
        SupabaseAdmin(URL, "chave").apagar_login(uuid.uuid4())

    assert erro.value.status == 503


def test_login_usa_chave_publica_e_grant_password(requisicoes):
    enviadas = requisicoes(httpx2.Response(200, json={"access_token": "a", "refresh_token": "r"}))

    sessao = SupabaseLogin(URL, "chave-publica").entrar_com_senha("ana@lorenzi.com", "senha")

    metodo, url, kwargs = enviadas[0]
    assert sessao["access_token"] == "a"
    assert (metodo, url) == ("POST", f"{URL}/auth/v1/token")
    assert kwargs["params"] == {"grant_type": "password"}
    assert kwargs["headers"] == {"apikey": "chave-publica"}


@pytest.mark.parametrize("classe", [SupabaseAdmin, SupabaseLogin])
def test_sem_chave_no_env_avisa_qual_falta(classe):
    with pytest.raises(RuntimeError, match="precisam estar no .env"):
        classe(URL, None)
