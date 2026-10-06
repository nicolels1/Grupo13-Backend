from fastapi import FastAPI

from src.middlewares.cors import configurar_cors
from src.routes import health

app = FastAPI(title="Casa Lorenzi API")
configurar_cors(app)

app.include_router(health.router)
