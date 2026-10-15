"""adiciona indices das consultas

Revision ID: 67e3435e7a07
Revises: e7a1c4f20b31
Create Date: 2026-10-14

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '67e3435e7a07'
down_revision: Union[str, Sequence[str], None] = 'e7a1c4f20b31'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# O PostgreSQL não cria índice nas chaves estrangeiras. Entram só as colunas que as consultas da API
# filtram ou ordenam (cada índice está comentado no model); colunas de registro, como "quem cancelou",
# ficam sem índice. Estoque por variante e variantes por produto já usam a PK de ESTOQUE e o UNIQUE de
# VARIANTE. Com as tabelas pequenas do case, cada índice é criado em instantes; nenhum GRANT é necessário
INDICES = [
    ("ix_chamado_id_cliente", "chamado", ["id_cliente"]),
    ("ix_historico_chamado_id_chamado", "historico_chamado", ["id_chamado"]),
    ("ix_imagem_produto_id_produto", "imagem_produto", ["id_produto"]),
    ("ix_item_pedido_id_pedido", "item_pedido", ["id_pedido"]),
    ("ix_item_transferencia_id_transferencia", "item_transferencia", ["id_transferencia"]),
    ("ix_mensagem_id_chamado", "mensagem", ["id_chamado"]),
    ("ix_movimentacao_estoque_criado_em", "movimentacao_estoque", ["criado_em"]),
    ("ix_movimentacao_estoque_id_pedido", "movimentacao_estoque", ["id_pedido"]),
    ("ix_movimentacao_estoque_variante_unidade_criado_em", "movimentacao_estoque",
     ["id_variante", "id_unidade", "criado_em"]),
    ("ix_pagamento_id_pedido", "pagamento", ["id_pedido"]),
    ("ix_pedido_criado_em", "pedido", ["criado_em"]),
    ("ix_pedido_id_cliente_criado_em", "pedido", ["id_cliente", "criado_em"]),
    ("ix_produto_id_categoria", "produto", ["id_categoria"]),
]


def upgrade() -> None:
    """Upgrade schema."""
    for nome, tabela, colunas in INDICES:
        op.create_index(nome, tabela, colunas, unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    for nome, tabela, _ in reversed(INDICES):
        op.drop_index(nome, table_name=tabela)
