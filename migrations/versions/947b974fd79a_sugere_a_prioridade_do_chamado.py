"""sugere a prioridade do chamado

Revision ID: 947b974fd79a
Revises: cef21782419d
Create Date: 2026-10-28 20:43:39.712992

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '947b974fd79a'
down_revision: Union[str, Sequence[str], None] = 'cef21782419d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Prioridade sugerida pela IA ao abrir o chamado (Entrega 4), em colunas separadas da prioridade: a IA nunca decide,
# quem assume confirma ou troca (e isso vai para o histórico como decisão da pessoa). Comparar as duas mede o acerto.
# Só colunas novas, vazias nos chamados que já existem


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('chamado', sa.Column('prioridade_sugerida', sa.String(length=10), nullable=True))
    op.add_column('chamado', sa.Column('motivo_sugerido', sa.String(length=200), nullable=True))
    op.add_column('chamado', sa.Column('sugerida_em', sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        op.f('ck_chamado_prioridade_sugerida_valida'), 'chamado', "prioridade_sugerida IN ('baixa', 'media', 'alta')"
    )
    op.create_check_constraint(
        op.f('ck_chamado_sugestao_com_data'), 'chamado', "(prioridade_sugerida IS NULL) = (sugerida_em IS NULL)"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f('ck_chamado_sugestao_com_data'), 'chamado', type_='check')
    op.drop_constraint(op.f('ck_chamado_prioridade_sugerida_valida'), 'chamado', type_='check')
    op.drop_column('chamado', 'sugerida_em')
    op.drop_column('chamado', 'motivo_sugerido')
    op.drop_column('chamado', 'prioridade_sugerida')
