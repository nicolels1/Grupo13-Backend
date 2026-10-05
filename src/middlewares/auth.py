import jwt
from jwt import PyJWKClient
from fastapi import Header, HTTPException
from src.config.settings import SUPABASE_URL

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
    except jwt.PyJWKClientConnectionError:
        # não conseguiu buscar a chave pública no Supabase: o problema é do servidor, não do token
        raise HTTPException(status_code=503, detail="Serviço de autenticação indisponível")
    except (jwt.InvalidTokenError, jwt.PyJWKClientError) as e:
        # PyJWKClientError: token assinado por uma chave que o Supabase não publica (ex.: outro projeto)
        raise HTTPException(status_code=401, detail=f"Token inválido: {str(e)}")

    return payload["sub"]