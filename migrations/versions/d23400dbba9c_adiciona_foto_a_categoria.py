"""adiciona foto a categoria

Revision ID: d23400dbba9c
Revises: 5809c1f5a848
Create Date: 2026-10-08 00:30:15.341164

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd23400dbba9c'
down_revision: Union[str, Sequence[str], None] = '5809c1f5a848'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# foto da categoria no carrossel da página inicial: o caminho no Storage (bucket produtos, pasta
# categorias/), nunca a URL. Opcional: categoria sem foto aparece no bloco de cor. O usuário restrito
# da API já lê e altera a tabela categoria_produto inteira (migration 56799f788354): nada a liberar


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('categoria_produto', sa.Column('caminho_imagem', sa.String(length=500), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('categoria_produto', 'caminho_imagem')
