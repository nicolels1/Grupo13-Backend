import os

# auth e banco leem as variáveis ao serem importados; sem .env (ex.: CI) usam valores fictícios.
# Nenhum teste conecta de verdade: create_engine não abre conexão até a primeira consulta.
os.environ.setdefault("SUPABASE_URL", "https://teste.supabase.co")
os.environ.setdefault("DATABASE_URL", "postgresql://teste:teste@localhost:5432/teste")
