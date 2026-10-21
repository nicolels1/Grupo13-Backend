"""adiciona desconto ao pedido

Revision ID: df6708e2d21e
Revises: 1d76b89051b6
Create Date: 2026-10-20 21:12:36.987109

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'df6708e2d21e'
down_revision: Union[str, Sequence[str], None] = '1d76b89051b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Desconto do pedido (V18, registro de decisões da migração da Vulto): o total da Vulto já vem com o
# desconto, e a coluna deixa total = itens − desconto + frete. A Lorenzi não dá desconto: os pedidos
# que já existem e os novos ficam com zero


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('pedido', sa.Column('valor_desconto', sa.Numeric(precision=10, scale=2), server_default='0', nullable=False))
    op.drop_constraint(op.f('ck_pedido_valores_nao_negativos'), 'pedido', type_='check')
    op.create_check_constraint(
        op.f('ck_pedido_valores_nao_negativos'), 'pedido',
        "valor_frete >= 0 AND valor_total >= 0 AND valor_desconto >= 0",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f('ck_pedido_valores_nao_negativos'), 'pedido', type_='check')
    op.create_check_constraint(
        op.f('ck_pedido_valores_nao_negativos'), 'pedido', "valor_frete >= 0 AND valor_total >= 0"
    )
    op.drop_column('pedido', 'valor_desconto')
