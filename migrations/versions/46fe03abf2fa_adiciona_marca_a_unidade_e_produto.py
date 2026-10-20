"""adiciona marca a unidade e produto

Revision ID: 46fe03abf2fa
Revises: 91a5ddfd1a16
Create Date: 2026-10-20 13:43:50.157457

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '46fe03abf2fa'
down_revision: Union[str, Sequence[str], None] = '91a5ddfd1a16'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Rede dona da unidade e do produto (P1, registro de decisões da migração da Vulto): uma plataforma
# só, com cada rede visível. O que já existe é da Lorenzi e recebe o valor padrão. O usuário restrito
# da API já lê, insere e altera as duas tabelas inteiras (migration 56799f788354): nada a liberar


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('produto', sa.Column('marca', sa.String(length=10), server_default='lorenzi', nullable=False))
    op.add_column('unidade', sa.Column('marca', sa.String(length=10), server_default='lorenzi', nullable=False))
    op.create_check_constraint(op.f('ck_produto_marca_valida'), 'produto', "marca IN ('lorenzi', 'vulto')")
    op.create_check_constraint(op.f('ck_unidade_marca_valida'), 'unidade', "marca IN ('lorenzi', 'vulto')")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f('ck_unidade_marca_valida'), 'unidade', type_='check')
    op.drop_constraint(op.f('ck_produto_marca_valida'), 'produto', type_='check')
    op.drop_column('unidade', 'marca')
    op.drop_column('produto', 'marca')
