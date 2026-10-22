"""cria permissoes de marca

Revision ID: c7d2e9a41f60
Revises: b11e7feaaa73
Create Date: 2026-10-21 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c7d2e9a41f60'
down_revision: Union[str, Sequence[str], None] = 'b11e7feaaa73'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Que marca cada pessoa da equipe vê nas listas (estoque, transferências, pedidos, chamados e Visão Geral):
# o Admin dá uma, a outra ou as duas no modelo de acesso ou como exceção. Os modelos que já existem
# recebem as duas, para ninguém perder o que já vê; o Admin tira a marca de quem só deve ver a outra
PERMISSOES = [
    ("ver_marca_lorenzi", "Ver a Casa Lorenzi nas listas da equipe"),
    ("ver_marca_vulto", "Ver a Vulto nas listas da equipe"),
]


def upgrade() -> None:
    """Upgrade schema."""
    for codigo, descricao in PERMISSOES:
        op.execute(f"INSERT INTO permissao (codigo, descricao) VALUES ('{codigo}', '{descricao}')")
        op.execute(f"""
            INSERT INTO modelo_permissao (id_modelo, id_permissao)
            SELECT m.id_modelo, p.id_permissao FROM modelo_acesso m, permissao p
            WHERE p.codigo = '{codigo}'
        """)


def downgrade() -> None:
    """Downgrade schema."""
    for codigo, _ in PERMISSOES:
        filtro = f"id_permissao = (SELECT id_permissao FROM permissao WHERE codigo = '{codigo}')"
        op.execute(f"DELETE FROM usuario_permissao_excecao WHERE {filtro}")
        op.execute(f"DELETE FROM modelo_permissao WHERE {filtro}")
        op.execute(f"DELETE FROM permissao WHERE codigo = '{codigo}'")
