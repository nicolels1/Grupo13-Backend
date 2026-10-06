import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from src.use_cases.erros import ErroNegocio

logger = logging.getLogger(__name__)

# códigos do PostgreSQL (SQLSTATE) para as restrições do banco
CHECK_VIOLATION = "23514"
UNIQUE_VIOLATION = "23505"
FOREIGN_KEY_VIOLATION = "23503"
NOT_NULL_VIOLATION = "23502"

# mensagens genéricas: nome de tabela e de restrição ficam só no log
MENSAGENS_DO_BANCO = {
    CHECK_VIOLATION: (422, "Dados inválidos"),
    UNIQUE_VIOLATION: (409, "Registro já existe"),
    FOREIGN_KEY_VIOLATION: (422, "Registro relacionado não existe ou ainda está em uso"),
    NOT_NULL_VIOLATION: (422, "Campo obrigatório ausente"),
}


def tratar_erro_negocio(request: Request, erro: ErroNegocio) -> JSONResponse:
    logger.info("%s %s recusado (%s): %s", request.method, request.url.path, erro.status_code, erro.mensagem)
    return JSONResponse(status_code=erro.status_code, content={"detail": erro.mensagem})


# restrições do banco (CHECK, UNIQUE, FK e triggers) viram 4xx em vez de 500
def tratar_erro_banco(request: Request, erro: IntegrityError) -> JSONResponse:
    original = erro.orig
    codigo = getattr(original, "sqlstate", None)
    diagnostico = getattr(original, "diag", None)
    restricao = getattr(diagnostico, "constraint_name", None)
    logger.warning("%s %s recusado pelo banco: %s", request.method, request.url.path, original)

    # trigger com RAISE EXCEPTION ... check_violation: a mensagem foi escrita para o usuário
    # (ex.: "Estoque insuficiente"); um CHECK da tabela traz o nome da restrição e não é exposto
    if codigo == CHECK_VIOLATION and restricao is None and diagnostico is not None:
        return JSONResponse(status_code=422, content={"detail": diagnostico.message_primary})

    if codigo in MENSAGENS_DO_BANCO:
        status_code, mensagem = MENSAGENS_DO_BANCO[codigo]
        return JSONResponse(status_code=status_code, content={"detail": mensagem})

    raise erro


def configurar_erros(app: FastAPI) -> None:
    app.add_exception_handler(ErroNegocio, tratar_erro_negocio)
    app.add_exception_handler(IntegrityError, tratar_erro_banco)
