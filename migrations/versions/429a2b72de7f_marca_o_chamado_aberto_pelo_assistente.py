"""marca o chamado aberto pelo assistente

Revision ID: 429a2b72de7f
Revises: e10623693e07
Create Date: 2026-10-29 10:12:41.318204

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '429a2b72de7f'
down_revision: Union[str, Sequence[str], None] = 'e10623693e07'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Assistente do cliente (Entrega 4): o chamado diz se foi aberto pelo assistente, em vez de a tela adivinhar
# pelo canal (whatsapp também é o canal dos atendimentos migrados da Vulto). Os chamados que já existem pelo
# Telegram só podem ter vindo do assistente


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'chamado',
        sa.Column('aberto_pelo_assistente', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    )
    op.execute("UPDATE chamado SET aberto_pelo_assistente = true WHERE canal_externo = 'telegram'")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('chamado', 'aberto_pelo_assistente')
