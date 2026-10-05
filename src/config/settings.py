import os
from pathlib import Path
from dotenv import load_dotenv

# aponta direto pro .env na raiz do Grupo13-Backend, não importa de onde é chamado
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

SUPABASE_URL = os.getenv("SUPABASE_URL")

# API usa o pooler do Supabase; migrations (Alembic) usam a conexão direta
DATABASE_URL = os.getenv("DATABASE_URL")
DATABASE_URL_DIRECT = os.getenv("DATABASE_URL_DIRECT")

# Sites (frontends) que podem chamar a API pelo navegador, separados por vírgula.
# Sem a variável, libera só o frontend local do Vite.
CORS_ORIGINS = [
    origem.strip().rstrip("/")
    for origem in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
    if origem.strip()
]
# Opcional: expressão regular para liberar vários endereços, como os previews da Vercel
CORS_ORIGIN_REGEX = os.getenv("CORS_ORIGIN_REGEX") or None
