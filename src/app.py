from fastapi import FastAPI

from src.config.logs import configurar_logs
from src.middlewares.cors import configurar_cors
from src.middlewares.erros import configurar_erros
from src.middlewares.requisicao import configurar_requisicao
from src.routes import (
    atendimento, catalogo, contas, enderecos, estoque, health, modelos_acesso, pedidos, transferencias,
    unidades, usuarios, vendas,
)

configurar_logs()

app = FastAPI(title="Casa Lorenzi API")
configurar_erros(app)
configurar_requisicao(app)
# o último middleware registrado fica por fora: o CORS vai também nas respostas de erro
configurar_cors(app)

app.include_router(health.router)
app.include_router(contas.router)
app.include_router(estoque.router)
app.include_router(catalogo.router)
app.include_router(unidades.router)
app.include_router(transferencias.router)
app.include_router(modelos_acesso.router)
app.include_router(atendimento.router)
app.include_router(usuarios.router)
app.include_router(enderecos.router)
app.include_router(pedidos.router)
app.include_router(vendas.router)
