import os

# o middleware de auth monta a URL do Supabase ao ser importado; sem .env (ex.: CI) usa um valor fictício
os.environ.setdefault("SUPABASE_URL", "https://teste.supabase.co")
