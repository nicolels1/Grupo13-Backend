"""aceita chamado migrado sem cliente

Revision ID: b11e7feaaa73
Revises: 9da6b1aa2392
Create Date: 2026-10-21 22:30:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b11e7feaaa73'
down_revision: Union[str, Sequence[str], None] = '9da6b1aa2392'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Atendimento da Vulto sem cliente (A5, A6, A7, registro de decisões da migração): quem escreveu não foi
# achado pelo pedido nem pelo contato, e o chamado entra só com o canal e o contato externos (P6). O chamado
# feito na plataforma continua exigindo exatamente um cliente; sem cliente, só o migrado (com canal externo)


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(op.f('ck_chamado_um_cliente'), 'chamado', type_='check')
    op.create_check_constraint(
        op.f('ck_chamado_um_cliente_ou_externo'), 'chamado',
        "num_nonnulls(id_cliente, id_cliente_sem_conta) = 1 OR "
        "(id_cliente IS NULL AND id_cliente_sem_conta IS NULL AND canal_externo IS NOT NULL)",
    )


def downgrade() -> None:
    """Downgrade schema."""
    # só desfaz enquanto nenhum chamado migrado sem cliente estiver no banco
    op.drop_constraint(op.f('ck_chamado_um_cliente_ou_externo'), 'chamado', type_='check')
    op.create_check_constraint(
        op.f('ck_chamado_um_cliente'), 'chamado', "num_nonnulls(id_cliente, id_cliente_sem_conta) = 1"
    )
