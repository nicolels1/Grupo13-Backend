"""guarda o rascunho do chamado na conversa

Revision ID: d70099d5cb80
Revises: 429a2b72de7f
Create Date: 2026-10-29 14:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd70099d5cb80'
down_revision: Union[str, Sequence[str], None] = '429a2b72de7f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Assistente do cliente (Entrega 4): o resumo que a IA montou fica guardado na conversa enquanto o cliente
# confere e aperta [Enviar], para o chamado abrir só com o que ele viu. Só existe na etapa confirmando_chamado,
# que até aqui nenhuma conversa usou


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('conversa_canal', sa.Column('rascunho_chamado', postgresql.JSONB(), nullable=True))
    op.create_check_constraint(
        op.f('ck_conversa_canal_rascunho_so_confirmando'),
        'conversa_canal',
        "(etapa = 'confirmando_chamado') = (rascunho_chamado IS NOT NULL)",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f('ck_conversa_canal_rascunho_so_confirmando'), 'conversa_canal', type_='check')
    op.drop_column('conversa_canal', 'rascunho_chamado')
