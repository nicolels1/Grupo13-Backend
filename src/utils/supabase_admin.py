import uuid

import httpx2

from src.config.settings import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL


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
        resposta.raise_for_status()
        return uuid.UUID(resposta.json()["id"])

    def apagar_login(self, id_usuario: uuid.UUID) -> None:
        resposta = httpx2.delete(f"{self.base}/{id_usuario}", headers=self.headers)
        resposta.raise_for_status()
