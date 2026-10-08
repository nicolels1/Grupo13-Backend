"""libera apagar foto de produto

Revision ID: e7a1c4f20b31
Revises: d23400dbba9c
Create Date: 2026-10-08

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e7a1c4f20b31'
down_revision: Union[str, Sequence[str], None] = 'd23400dbba9c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# foto de produto entra nas exceções do "nada é apagado" (migration 56799f788354): nenhuma tabela
# aponta para imagem_produto e foto errada não é histórico. A API apaga a linha e o arquivo no Storage
PAPEL = "api_casalorenzi"


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(f"GRANT DELETE ON imagem_produto TO {PAPEL};")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(f"REVOKE DELETE ON imagem_produto FROM {PAPEL};")
