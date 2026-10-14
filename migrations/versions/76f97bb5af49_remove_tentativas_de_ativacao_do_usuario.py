"""remove tentativas de ativacao do usuario

Revision ID: 76f97bb5af49
Revises: e08fd1b28bcf
Create Date: 2026-10-06 00:11:06.581373

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '76f97bb5af49'
down_revision: Union[str, Sequence[str], None] = 'e08fd1b28bcf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ativação sem limite de tentativas: vale só o prazo do link (seção 5 do case)
def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column('usuario', 'tentativas_ativacao')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('usuario', sa.Column('tentativas_ativacao', sa.Integer(), server_default='0', nullable=False))
