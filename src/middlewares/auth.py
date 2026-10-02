import os
import jwt
from pathlib import Path
from jwt import PyJWKClient
from fastapi import Header, HTTPException
from dotenv import load_dotenv

# aponta direto pro .env na raiz do Grupo13-Backend, não importa de onde é chamado
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

SUPABASE_URL = os.getenv("SUPABASE_URL")
JWKS_URL = f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json"

# Busca e guarda em cache a chave pública do Supabase
jwks_client = PyJWKClient(JWKS_URL)

def get_current_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token ausente")

    token = authorization.replace("Bearer ", "")
    try:
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256"],
            audience="authenticated",
        )
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Token inválido: {str(e)}")

    return payload["sub"]