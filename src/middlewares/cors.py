from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.config.settings import CORS_ORIGINS, CORS_ORIGIN_REGEX


# Libera o frontend (outro domínio, ex.: Vercel) a chamar a API pelo navegador.
# O login vai no header Authorization, não em cookie, então não precisa de allow_credentials.
def configurar_cors(app: FastAPI, origens=CORS_ORIGINS, origem_regex=CORS_ORIGIN_REGEX):
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origens,
        allow_origin_regex=origem_regex,
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type"],
    )
