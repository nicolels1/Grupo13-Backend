import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.config.logs import request_id_atual

logger = logging.getLogger(__name__)


# dá um id a cada requisição (vai nos logs e no header X-Request-ID) e transforma
# erro inesperado em 500 com mensagem genérica; o detalhe fica só no log
def configurar_requisicao(app: FastAPI) -> None:
    @app.middleware("http")
    async def registrar_requisicao(request: Request, call_next):
        request_id = uuid.uuid4().hex[:8]
        marcador = request_id_atual.set(request_id)
        try:
            resposta = await call_next(request)
        except Exception:
            logger.exception("erro inesperado em %s %s", request.method, request.url.path)
            resposta = JSONResponse(status_code=500, content={"detail": "Erro interno do servidor"})
        finally:
            request_id_atual.reset(marcador)

        resposta.headers["X-Request-ID"] = request_id
        return resposta
