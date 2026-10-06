import uuid

import httpx2

from src.config.settings import SUPABASE_PUBLISHABLE_KEY, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL

TEMPO_LIMITE = 10  # segundos
BLOQUEIO = "876000h"  # 100 anos: o Supabase não tem bloqueio sem prazo


class ErroSupabase(Exception):
    # status: código HTTP da resposta (503 quando o Supabase nem respondeu);
    # codigo: error_code do Supabase, ex.: "email_exists", "weak_password", "invalid_credentials"
    def __init__(self, status: int, codigo: str | None, mensagem: str):
        super().__init__(f"Supabase recusou ({status}): {mensagem}")
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _conferir(resposta: httpx2.Response) -> None:
    # devolve a mensagem do Supabase (ex.: "Password should be at least 6 characters.")
    if resposta.is_error:
        try:
            corpo = resposta.json()
        except ValueError:
            corpo = {}
        codigo = corpo.get("error_code") or corpo.get("error")
        mensagem = corpo.get("msg") or corpo.get("error_description") or resposta.text
        raise ErroSupabase(resposta.status_code, codigo, mensagem)


def _enviar(metodo: str, url: str, **kwargs) -> httpx2.Response:
    try:
        resposta = httpx2.request(metodo, url, timeout=TEMPO_LIMITE, **kwargs)
    except httpx2.HTTPError as erro:
        raise ErroSupabase(503, "indisponivel", str(erro))
    _conferir(resposta)
    return resposta


# cliente da API de admin do Supabase Auth (cria e apaga logins); exige a service_role
class SupabaseAdmin:
    def __init__(self, url: str | None = SUPABASE_URL, chave: str | None = SUPABASE_SERVICE_ROLE_KEY):
        if not url or not chave:
            raise RuntimeError("SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY precisam estar no .env")
        self.base = f"{url}/auth/v1/admin/users"
        self.convite = f"{url}/auth/v1/invite"
        self.headers = {"apikey": chave, "Authorization": f"Bearer {chave}"}

    def criar_login(self, email: str, senha: str) -> uuid.UUID:
        # email_confirm: a conta já nasce confirmada, sem e-mail de confirmação
        resposta = _enviar(
            "POST", self.base, headers=self.headers,
            json={"email": email, "password": senha, "email_confirm": True},
        )
        return uuid.UUID(resposta.json()["id"])

    # marca o e-mail como confirmado (login criado antes e nunca confirmado não consegue entrar)
    def confirmar_email(self, id_usuario: uuid.UUID) -> None:
        _enviar("PUT", f"{self.base}/{id_usuario}", headers=self.headers, json={"email_confirm": True})

    def apagar_login(self, id_usuario: uuid.UUID) -> None:
        _enviar("DELETE", f"{self.base}/{id_usuario}", headers=self.headers)

    # cria o login sem senha e manda o e-mail de convite; a pessoa define a senha pelo link.
    # Com o e-mail padrão do Supabase, só chega para quem é da equipe do projeto (case: servidor próprio)
    def convidar(self, email: str) -> uuid.UUID:
        resposta = _enviar("POST", self.convite, headers=self.headers, json={"email": email})
        return uuid.UUID(resposta.json()["id"])

    # desativar uma conta bloqueia o login no Auth (case, seção 5); "none" desbloqueia
    def bloquear_login(self, id_usuario: uuid.UUID) -> None:
        _enviar("PUT", f"{self.base}/{id_usuario}", headers=self.headers, json={"ban_duration": BLOQUEIO})

    def desbloquear_login(self, id_usuario: uuid.UUID) -> None:
        _enviar("PUT", f"{self.base}/{id_usuario}", headers=self.headers, json={"ban_duration": "none"})


# login com e-mail e senha pela chave pública; usado no login por CPF,
# em que o backend descobre o e-mail sem mostrá-lo ao cliente
class SupabaseLogin:
    def __init__(self, url: str | None = SUPABASE_URL, chave: str | None = SUPABASE_PUBLISHABLE_KEY):
        if not url or not chave:
            raise RuntimeError("SUPABASE_URL e SUPABASE_PUBLISHABLE_KEY precisam estar no .env")
        self.url = f"{url}/auth/v1/token"
        self.headers = {"apikey": chave}

    # devolve a sessão do Supabase (access_token, refresh_token, expires_in, token_type...)
    def entrar_com_senha(self, email: str, senha: str) -> dict:
        resposta = _enviar(
            "POST", self.url, params={"grant_type": "password"}, headers=self.headers,
            json={"email": email, "password": senha},
        )
        return resposta.json()
