"""adiciona categoria pagamento ao chamado

Revision ID: 57c4e9a323e3
Revises: 474f40efc69c
Create Date: 2026-10-20 21:47:32.958109

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '57c4e9a323e3'
down_revision: Union[str, Sequence[str], None] = '474f40efc69c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Categoria pagamento (A4a, registro de decisões da migração da Vulto): problema de cobrança de quem pagou
# e ficou sem pedido válido. Vale também para os chamados novos da Lorenzi, que não tinha onde pôr esse
# problema. Nenhuma regra depende dela: só troca_devolucao libera a troca

CATEGORIAS = "'entrega', 'troca_devolucao', 'estorno', 'duvida', 'outros'"


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(op.f('ck_chamado_categoria_valida'), 'chamado', type_='check')
    op.create_check_constraint(
        op.f('ck_chamado_categoria_valida'), 'chamado',
        "categoria IN ('entrega', 'troca_devolucao', 'estorno', 'duvida', 'pagamento', 'outros')",
    )


def downgrade() -> None:
    """Downgrade schema."""
    # só desfaz enquanto nenhum chamado tiver a categoria pagamento
    op.drop_constraint(op.f('ck_chamado_categoria_valida'), 'chamado', type_='check')
    op.create_check_constraint(op.f('ck_chamado_categoria_valida'), 'chamado', f"categoria IN ({CATEGORIAS})")
