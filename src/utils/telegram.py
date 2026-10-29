import logging

import httpx2

from src.config import settings

logger = logging.getLogger(__name__)

URL_TELEGRAM = "https://api.telegram.org/bot{token}/sendMessage"
TEMPO_LIMITE = 10  # segundos
TAMANHO_MAXIMO = 4096  # caracteres por mensagem no Telegram


class ErroTelegram(Exception):
    pass


# mensagem do sistema numa conversa do bot (o cliente já deu /start, então o bot pode escrever primeiro).
# O token vai na URL: o erro nunca registra a URL nem a mensagem do httpx, que a traz
def enviar_telegram(chat_id: str, texto: str) -> None:
    if not settings.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN não configurado: mensagem não enviada")
        raise ErroTelegram("Envio pelo Telegram não configurado")
    try:
        resposta = httpx2.post(
            URL_TELEGRAM.format(token=settings.TELEGRAM_BOT_TOKEN), timeout=TEMPO_LIMITE,
            json={"chat_id": chat_id, "text": texto},
        )
    except httpx2.HTTPError as erro:
        logger.error("Telegram sem resposta (%s)", type(erro).__name__)
        raise ErroTelegram("Telegram sem resposta") from None
    if resposta.is_error:
        logger.error("Telegram recusou a mensagem (%s): %s", resposta.status_code, resposta.text)
        raise ErroTelegram(f"Telegram recusou a mensagem ({resposta.status_code})")
