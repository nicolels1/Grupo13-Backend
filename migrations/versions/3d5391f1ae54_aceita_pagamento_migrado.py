"""aceita pagamento migrado

Revision ID: 3d5391f1ae54
Revises: df6708e2d21e
Create Date: 2026-10-20 21:12:37.766157

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3d5391f1ae54'
down_revision: Union[str, Sequence[str], None] = 'df6708e2d21e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Pagamento migrado (P7, registro de decisões da migração da Vulto): a Vulto nunca guardou o método nem o
# estorno como lançamento. O pagamento e o estorno migrados têm a origem `migracao`, e o método
# `nao_informado` só é aceito no pagamento com essa origem. O estorno não precisa da regra: o trigger
# estorno_mesmo_metodo (migration b292949e2496) só aceita o método do pagamento original, então o
# `nao_informado` só chega a um estorno de pagamento migrado, como na devolução no balcão (V13)

METODOS = "'pix', 'cartao_credito', 'cartao_debito', 'dinheiro'"
ORIGENS = "'cancelamento', 'atendimento', 'balcao'"


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(op.f('ck_pagamento_metodo_valido'), 'pagamento', type_='check')
    op.create_check_constraint(
        op.f('ck_pagamento_metodo_valido'), 'pagamento', f"metodo IN ({METODOS}, 'nao_informado')"
    )
    op.drop_constraint(op.f('ck_pagamento_origem_valida'), 'pagamento', type_='check')
    op.create_check_constraint(
        op.f('ck_pagamento_origem_valida'), 'pagamento', f"origem IN ({ORIGENS}, 'migracao')"
    )
    # o estorno sempre tem origem; o pagamento só tem a da migração
    op.drop_constraint(op.f('ck_pagamento_origem_so_em_estorno'), 'pagamento', type_='check')
    op.create_check_constraint(
        op.f('ck_pagamento_origem_do_tipo'), 'pagamento',
        "(tipo = 'estorno' AND origem IS NOT NULL) "
        "OR (tipo = 'pagamento' AND (origem IS NULL OR origem = 'migracao'))",
    )
    op.create_check_constraint(
        op.f('ck_pagamento_nao_informado_so_migrado'), 'pagamento',
        "metodo <> 'nao_informado' OR tipo = 'estorno' OR origem IS NOT DISTINCT FROM 'migracao'",
    )


def downgrade() -> None:
    """Downgrade schema."""
    # só desfaz enquanto nenhum pagamento migrado estiver no banco
    op.drop_constraint(op.f('ck_pagamento_nao_informado_so_migrado'), 'pagamento', type_='check')
    op.drop_constraint(op.f('ck_pagamento_origem_do_tipo'), 'pagamento', type_='check')
    op.create_check_constraint(
        op.f('ck_pagamento_origem_so_em_estorno'), 'pagamento', "(tipo = 'estorno') = (origem IS NOT NULL)"
    )
    op.drop_constraint(op.f('ck_pagamento_origem_valida'), 'pagamento', type_='check')
    op.create_check_constraint(op.f('ck_pagamento_origem_valida'), 'pagamento', f"origem IN ({ORIGENS})")
    op.drop_constraint(op.f('ck_pagamento_metodo_valido'), 'pagamento', type_='check')
    op.create_check_constraint(op.f('ck_pagamento_metodo_valido'), 'pagamento', f"metodo IN ({METODOS})")
