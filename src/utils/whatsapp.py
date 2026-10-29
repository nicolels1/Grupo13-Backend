import logging
import re
from datetime import timedelta

import httpx2

from src.config import settings

logger = logging.getLogger(__name__)

URL_WHATSAPP = "https://graph.facebook.com/{versao}/{numero}/messages"
VERSAO_API = "v24.0"
TEMPO_LIMITE = 10  # segundos
TAMANHO_MAXIMO = 4096  # caracteres por mensagem de texto no WhatsApp
# a Meta só aceita mensagem livre até 24h depois da última mensagem do cliente; depois, só modelo aprovado
JANELA_DE_ATENDIMENTO = timedelta(hours=24)


class ErroWhatsapp(Exception):
    pass


# mensagem de texto pela API da Meta (WhatsApp Cloud API). O telefone vai só com os dígitos, como a Meta
# manda no webhook. O token vai no cabeçalho: o erro nunca registra a mensagem do httpx
def enviar_whatsapp(telefone: str, texto: str) -> None:
    if not settings.WHATSAPP_TOKEN or not settings.WHATSAPP_PHONE_NUMBER_ID:
        logger.error("WHATSAPP_TOKEN ou WHATSAPP_PHONE_NUMBER_ID não configurado: mensagem não enviada")
        raise ErroWhatsapp("Envio pelo WhatsApp não configurado")
    try:
        resposta = httpx2.post(
            URL_WHATSAPP.format(versao=VERSAO_API, numero=settings.WHATSAPP_PHONE_NUMBER_ID), timeout=TEMPO_LIMITE,
            headers={"Authorization": f"Bearer {settings.WHATSAPP_TOKEN}"},
            json={
                "messaging_product": "whatsapp",
                "to": re.sub(r"\D", "", telefone),
                "type": "text",
                "text": {"body": texto, "preview_url": True},
            },
        )
    except httpx2.HTTPError as erro:
        logger.error("WhatsApp sem resposta (%s)", type(erro).__name__)
        raise ErroWhatsapp("WhatsApp sem resposta") from None
    if resposta.is_error:
        logger.error("WhatsApp recusou a mensagem (%s): %s", resposta.status_code, resposta.text)
        raise ErroWhatsapp(f"WhatsApp recusou a mensagem ({resposta.status_code})")
