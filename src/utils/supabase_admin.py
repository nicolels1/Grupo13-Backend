import uuid

import httpx2

from src.config.settings import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL


class ErroSupabase(Exception):
    pass


def _conferir(resposta: httpx2.Response) -> None:
    # devolve a mensagem do Supabase (ex.: "Password should be at least 6 characters.")
    if resposta.is_error:
        try:
            msg = resposta.json().get("msg") or resposta.text
        except ValueError:
            msg = resposta.text
        raise ErroSupabase(f"Supabase recusou ({resposta.status_code}): {msg}")


# cliente da API de admin do Supabase Auth (cria e apaga logins); exige a service_role
class SupabaseAdmin:
    def __init__(self, url: str | None = SUPABASE_URL, chave: str | None = SUPABASE_SERVICE_ROLE_KEY):
        if not url or not chave:
            raise RuntimeError("SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY precisam estar no .env")
        self.base = f"{url}/auth/v1/admin/users"
        self.headers = {"apikey": chave, "Authorization": f"Bearer {chave}"}

    def criar_login(self, email: str, senha: str) -> uuid.UUID:
        # email_confirm: a conta já nasce confirmada, sem e-mail de confirmação
        resposta = httpx2.post(
            self.base,
            headers=self.headers,
            json={"email": email, "password": senha, "email_confirm": True},
        )
        _conferir(resposta)
        return uuid.UUID(resposta.json()["id"])

    # marca o e-mail como confirmado (login criado antes e nunca confirmado não consegue entrar)
    def confirmar_email(self, id_usuario: uuid.UUID) -> None:
        _conferir(httpx2.put(f"{self.base}/{id_usuario}", headers=self.headers, json={"email_confirm": True}))

    def apagar_login(self, id_usuario: uuid.UUID) -> None:
        _conferir(httpx2.delete(f"{self.base}/{id_usuario}", headers=self.headers))
