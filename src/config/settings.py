import os
from pathlib import Path
from dotenv import load_dotenv

# aponta direto pro .env na raiz do Grupo13-Backend, não importa de onde é chamado
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

SUPABASE_URL = os.getenv("SUPABASE_URL")
# chave de admin do Supabase: só no backend, nunca no front (ADR 0008)
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
# chave pública do Supabase: usada só no login por CPF, feito pelo backend
SUPABASE_PUBLISHABLE_KEY = os.getenv("SUPABASE_PUBLISHABLE_KEY")

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

# Endereço da plataforma do cliente, para os links que o assistente manda (peça, carrinho)
SITE_URL = (os.getenv("SITE_URL") or "http://localhost:5173").rstrip("/")

# Chave das rotas /assistente: o n8n manda no cabeçalho X-Assistente-Chave. Vazia, as rotas ficam fechadas
ASSISTENTE_CHAVE = os.getenv("ASSISTENTE_CHAVE") or None

# Pasta data/ do vulto-legado-back, lida pela migração da Vulto (migracao_vulto/); a API não usa
VULTO_DATA_DIR = os.getenv("VULTO_DATA_DIR") or None

# E-mail que o sistema manda (código do assistente), pela API do Brevo: o Render grátis bloqueia SMTP.
# Sem a chave, o envio falha com aviso no log
BREVO_API_KEY = os.getenv("BREVO_API_KEY") or None
EMAIL_REMETENTE = os.getenv("EMAIL_REMETENTE") or "casa.lorenzi.contato@gmail.com"
