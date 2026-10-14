"""busca sem acento e por semelhanca

Revision ID: 5809c1f5a848
Revises: 221981ca8429
Create Date: 2026-10-07

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '5809c1f5a848'
down_revision: Union[str, Sequence[str], None] = '221981ca8429'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Busca da vitrine sem depender de acento nem da palavra exata: unaccent tira os acentos e o
# pg_trgm compara palavras parecidas ("camisa" ~ "camiseta", "calsa" ~ "calça"). No Supabase as
# extensões ficam no esquema extensions, que o usuário restrito da API não enxerga; por isso duas
# funções do dono respondem só o necessário, como login_por_email (migration 56799f788354)
PAPEL = "api_casalorenzi"

FUNCOES = {
    "texto_de_busca(text)": """
        CREATE FUNCTION texto_de_busca(texto text) RETURNS text
        LANGUAGE sql IMMUTABLE PARALLEL SAFE SECURITY DEFINER SET search_path = '' AS $$
            SELECT lower(extensions.unaccent('extensions.unaccent'::regdictionary, coalesce(texto, '')))
        $$;
    """,
    # semelhança (0 a 1) entre o termo e a palavra inteira mais parecida do texto. A versão "strict"
    # não mistura pedaços de duas palavras: com a comum, "calca" parecia "camiseta basica"
    "semelhanca_de_palavra(text, text)": """
        CREATE FUNCTION semelhanca_de_palavra(termo text, texto text) RETURNS real
        LANGUAGE sql IMMUTABLE PARALLEL SAFE SECURITY DEFINER SET search_path = '' AS $$
            SELECT extensions.strict_word_similarity(termo, texto)
        $$;
    """,
}


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE SCHEMA IF NOT EXISTS extensions;")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent WITH SCHEMA extensions;")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA extensions;")
    for assinatura, sql in FUNCOES.items():
        op.execute(sql)
        op.execute(f"REVOKE ALL ON FUNCTION {assinatura} FROM PUBLIC, anon, authenticated;")
        op.execute(f"GRANT EXECUTE ON FUNCTION {assinatura} TO {PAPEL};")


def downgrade() -> None:
    """Downgrade schema."""
    for assinatura in FUNCOES:
        op.execute(f"DROP FUNCTION IF EXISTS {assinatura};")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm;")
    op.execute("DROP EXTENSION IF EXISTS unaccent;")
