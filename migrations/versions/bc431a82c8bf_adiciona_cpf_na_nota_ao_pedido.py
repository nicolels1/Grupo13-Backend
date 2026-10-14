"""adiciona cpf na nota ao pedido

Revision ID: bc431a82c8bf
Revises: 03aeb347f6cf
Create Date: 2026-10-07 03:00:13.008790

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bc431a82c8bf'
down_revision: Union[str, Sequence[str], None] = '03aeb347f6cf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# CPF informado na venda física de quem ainda não tem conta (ADR 0014). O usuário restrito
# da API já lê, insere e altera a tabela pedido inteira (migration 56799f788354): nada a liberar


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('pedido', sa.Column('cpf_nota', sa.String(length=11), nullable=True))
    op.create_check_constraint(
        op.f('ck_pedido_cpf_nota_so_loja_fisica'), 'pedido', "cpf_nota IS NULL OR canal = 'loja_fisica'"
    )
    op.create_check_constraint(op.f('ck_pedido_cpf_nota_so_digitos'), 'pedido', "cpf_nota ~ '^[0-9]{11}$'")
    op.create_index(
        'ix_pedido_cpf_nota_sem_cliente', 'pedido', ['cpf_nota'], unique=False,
        postgresql_where=sa.text('id_cliente IS NULL'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_pedido_cpf_nota_sem_cliente', table_name='pedido', postgresql_where=sa.text('id_cliente IS NULL'))
    op.drop_constraint(op.f('ck_pedido_cpf_nota_so_digitos'), 'pedido', type_='check')
    op.drop_constraint(op.f('ck_pedido_cpf_nota_so_loja_fisica'), 'pedido', type_='check')
    op.drop_column('pedido', 'cpf_nota')
