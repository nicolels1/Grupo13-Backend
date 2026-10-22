import os

# auth e banco leem as variáveis ao serem importados; sem .env (ex.: CI) usam valores fictícios.
# Nenhum teste conecta de verdade: create_engine não abre conexão até a primeira consulta.
os.environ.setdefault("SUPABASE_URL", "https://teste.supabase.co")
os.environ.setdefault("DATABASE_URL", "postgresql://teste:teste@localhost:5432/teste")

import pytest  # noqa: E402

from src.repositories import marca_repository  # noqa: E402

MARCA_DE = ["da_unidade", "do_pedido", "da_transferencia", "do_chamado", "do_produto", "da_variante", "da_imagem"]


# sem banco, a marca de um registro vem vazia (como a dúvida geral: passa); o teste da permissão de marca define a sua
@pytest.fixture(autouse=True)
def registro_sem_marca(monkeypatch):
    for nome in MARCA_DE:
        monkeypatch.setattr(marca_repository, nome, lambda db, id_registro: set())
