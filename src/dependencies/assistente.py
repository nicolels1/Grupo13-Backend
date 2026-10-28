import hmac
import logging

from fastapi import Header, HTTPException

from src.config import settings

logger = logging.getLogger(__name__)


# as rotas /assistente não têm login de cliente: quem chama é o n8n, que prova ser ele com a chave
# combinada nas variáveis de ambiente dos dois lados. Sem a chave configurada, as rotas ficam fechadas
def exige_chave_assistente(x_assistente_chave: str | None = Header(None)) -> None:
    if not settings.ASSISTENTE_CHAVE:
        logger.error("ASSISTENTE_CHAVE não configurada: rotas do assistente fechadas")
        raise HTTPException(status_code=503, detail="Assistente indisponível")
    if not x_assistente_chave or not hmac.compare_digest(
        x_assistente_chave.encode(), settings.ASSISTENTE_CHAVE.encode()
    ):
        raise HTTPException(status_code=401, detail="Chave do assistente inválida")
