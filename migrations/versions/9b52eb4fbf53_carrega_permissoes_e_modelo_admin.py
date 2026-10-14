"""carrega permissoes e modelo admin

Revision ID: 9b52eb4fbf53
Revises: d0a686154ec5
Create Date: 2026-10-05 17:27:50.538282

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9b52eb4fbf53'
down_revision: Union[str, Sequence[str], None] = 'd0a686154ec5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# lista fixa de permissões (seção 6 do case); gerenciar_contas, gerenciar_modelos_acesso
# e gerenciar_unidades são da Gestão: só o Admin tem, e não podem ser dadas por exceção
PERMISSOES = [
    ("gerenciar_contas", "Gerenciar contas"),
    ("gerenciar_modelos_acesso", "Gerenciar modelos de acesso"),
    ("gerenciar_catalogo", "Gerenciar produtos e categorias"),
    ("gerenciar_unidades", "Gerenciar unidades"),
    ("movimentar_estoque", "Movimentar estoque"),
    ("definir_estoque_minimo", "Definir estoque mínimo"),
    ("solicitar_transferencia", "Solicitar transferência"),
    ("enviar_transferencia", "Enviar transferência"),
    ("receber_transferencia", "Receber transferência"),
    ("registrar_venda_fisica", "Registrar venda física"),
    ("preparar_entregar_pedido", "Preparar e entregar pedido"),
    ("cancelar_pedido_equipe", "Cancelar pedido pela equipe"),
    ("corrigir_cadastro_cliente", "Corrigir cadastro de cliente"),
    ("atender_chamado", "Atender chamado"),
    ("moderar_avaliacoes", "Moderar avaliações"),
]

permissao = sa.table(
    "permissao",
    sa.column("codigo", sa.String),
    sa.column("descricao", sa.String),
)


def upgrade() -> None:
    """Upgrade schema."""
    op.bulk_insert(permissao, [{"codigo": c, "descricao": d} for c, d in PERMISSOES])
    op.execute("INSERT INTO modelo_acesso (nome, eh_admin, ativo) VALUES ('Admin', true, true)")
    op.execute("""
        INSERT INTO modelo_permissao (id_modelo, id_permissao)
        SELECT m.id_modelo, p.id_permissao
        FROM modelo_acesso m CROSS JOIN permissao p
        WHERE m.nome = 'Admin'
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("""
        DELETE FROM modelo_permissao
        WHERE id_modelo = (SELECT id_modelo FROM modelo_acesso WHERE nome = 'Admin')
    """)
    op.execute("DELETE FROM modelo_acesso WHERE nome = 'Admin'")
    codigos = ", ".join(f"'{c}'" for c, _ in PERMISSOES)
    op.execute(f"DELETE FROM permissao WHERE codigo IN ({codigos})")
