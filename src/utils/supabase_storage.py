from urllib.parse import quote

from src.config.settings import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL
from src.utils.supabase_admin import _enviar

VALIDADE_DO_LINK = 3600  # segundos: link temporário de arquivo privado (anexo, foto de avaliação)


# arquivo de bucket público (fotos de produto): não precisa de chave nem de assinatura
def url_publica(bucket: str, caminho: str) -> str:
    return f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{quote(caminho)}"


# cliente do Supabase Storage; só o backend envia arquivos, com a chave de serviço (case, seção 2).
# O banco guarda o caminho do arquivo, nunca a URL
class SupabaseStorage:
    def __init__(self, url: str | None = SUPABASE_URL, chave: str | None = SUPABASE_SERVICE_ROLE_KEY):
        if not url or not chave:
            raise RuntimeError("SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY precisam estar no .env")
        self.base = f"{url}/storage/v1"
        self.headers = {"apikey": chave, "Authorization": f"Bearer {chave}"}

    def enviar(self, bucket: str, caminho: str, conteudo: bytes, tipo: str) -> None:
        _enviar(
            "POST", f"{self.base}/object/{bucket}/{quote(caminho)}", content=conteudo,
            headers={**self.headers, "Content-Type": tipo, "x-upsert": "false"},
        )

    def apagar(self, bucket: str, caminho: str) -> None:
        _enviar("DELETE", f"{self.base}/object/{bucket}", headers=self.headers, json={"prefixes": [caminho]})

    # arquivo de bucket privado: link que expira
    def url_temporaria(self, bucket: str, caminho: str, segundos: int = VALIDADE_DO_LINK) -> str:
        resposta = _enviar(
            "POST", f"{self.base}/object/sign/{bucket}/{quote(caminho)}", headers=self.headers,
            json={"expiresIn": segundos},
        )
        return f"{self.base}{resposta.json()['signedURL']}"
