import logging

import httpx2

from src.config import settings

logger = logging.getLogger(__name__)

URL_BREVO = "https://api.brevo.com/v3/smtp/email"
TEMPO_LIMITE = 10  # segundos
NOME_REMETENTE = "Casa Lorenzi"


class ErroEmail(Exception):
    pass


# e-mail transacional pela API do Brevo (uma chamada HTTP; o Render grátis bloqueia SMTP)
def enviar_email(para: str, assunto: str, texto: str) -> None:
    if not settings.BREVO_API_KEY:
        logger.error("BREVO_API_KEY não configurada: e-mail não enviado")
        raise ErroEmail("Envio de e-mail não configurado")
    try:
        resposta = httpx2.post(
            URL_BREVO, timeout=TEMPO_LIMITE,
            headers={"api-key": settings.BREVO_API_KEY, "accept": "application/json"},
            json={
                "sender": {"name": NOME_REMETENTE, "email": settings.EMAIL_REMETENTE},
                "to": [{"email": para}],
                "subject": assunto,
                "textContent": texto,
            },
        )
    except httpx2.HTTPError as erro:
        logger.error("Brevo sem resposta: %s", erro)
        raise ErroEmail("Brevo sem resposta") from erro
    if resposta.is_error:
        logger.error("Brevo recusou o e-mail (%s): %s", resposta.status_code, resposta.text)
        raise ErroEmail(f"Brevo recusou o e-mail ({resposta.status_code})")
