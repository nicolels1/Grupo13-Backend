"""adiciona canal de venda ao pedido

Revision ID: 1d76b89051b6
Revises: b49200db9df1
Create Date: 2026-10-20 21:12:36.272460

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1d76b89051b6'
down_revision: Union[str, Sequence[str], None] = 'b49200db9df1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Canal de venda (P3, registro de decisões da migração da Vulto): loja, site ou app, para o faturamento
# por canal. O `canal` continua sendo o do estoque (ADR 0004), e o site e o app vendem do mesmo estoque
# online. Os pedidos que já existem são da Lorenzi, que não tem app: loja física vira loja, online vira
# site. O usuário restrito da API já lê, insere e altera o pedido inteiro (migration 56799f788354)


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('pedido', sa.Column('canal_venda', sa.String(length=10), nullable=True))
    op.execute("UPDATE pedido SET canal_venda = CASE WHEN canal = 'loja_fisica' THEN 'loja' ELSE 'site' END")
    op.alter_column('pedido', 'canal_venda', existing_type=sa.String(length=10), nullable=False)
    op.create_check_constraint(
        op.f('ck_pedido_canal_venda_valido'), 'pedido', "canal_venda IN ('loja', 'site', 'app')"
    )
    # a venda na loja é a do estoque da loja física; site e app vendem do estoque online
    op.create_check_constraint(
        op.f('ck_pedido_canal_venda_do_canal'), 'pedido', "(canal = 'loja_fisica') = (canal_venda = 'loja')"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f('ck_pedido_canal_venda_do_canal'), 'pedido', type_='check')
    op.drop_constraint(op.f('ck_pedido_canal_venda_valido'), 'pedido', type_='check')
    op.drop_column('pedido', 'canal_venda')
