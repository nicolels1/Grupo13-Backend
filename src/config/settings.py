import os
from pathlib import Path
from dotenv import load_dotenv

# aponta direto pro .env na raiz do Grupo13-Backend, não importa de onde é chamado
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

SUPABASE_URL = os.getenv("SUPABASE_URL")

# API usa o pooler do Supabase; migrations (Alembic) usam a conexão direta
DATABASE_URL = os.getenv("DATABASE_URL")
DATABASE_URL_DIRECT = os.getenv("DATABASE_URL_DIRECT")
