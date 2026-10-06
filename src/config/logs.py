import logging
from contextvars import ContextVar

# id da requisição em andamento; definido em middlewares/requisicao.py
request_id_atual: ContextVar[str] = ContextVar("request_id", default="-")


class AdicionaRequestId(logging.Filter):
    def filter(self, registro: logging.LogRecord) -> bool:
        registro.request_id = request_id_atual.get()
        return True


# configura só os loggers do projeto (logging.getLogger(__name__) dentro de src/);
# os logs do Uvicorn continuam como estão
def configurar_logs(nivel: int = logging.INFO) -> None:
    logger = logging.getLogger("src")
    if logger.handlers:
        return

    saida = logging.StreamHandler()
    saida.addFilter(AdicionaRequestId())
    saida.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s request_id=%(request_id)s %(name)s: %(message)s", "%H:%M:%S"
    ))
    logger.addHandler(saida)
    logger.setLevel(nivel)
    logger.propagate = False
