from fastapi import FastAPI

from src.config.logs import configurar_logs
from src.middlewares.cors import configurar_cors
from src.middlewares.erros import configurar_erros
from src.middlewares.requisicao import configurar_requisicao
from src.routes import contas, health

configurar_logs()

app = FastAPI(title="Casa Lorenzi API")
configurar_erros(app)
configurar_requisicao(app)
# o último middleware registrado fica por fora: o CORS vai também nas respostas de erro
configurar_cors(app)

app.include_router(health.router)
app.include_router(contas.router)
