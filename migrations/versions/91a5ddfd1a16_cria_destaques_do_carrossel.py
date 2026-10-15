"""cria destaques do carrossel

Revision ID: 91a5ddfd1a16
Revises: 67e3435e7a07
Create Date: 2026-10-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '91a5ddfd1a16'
down_revision: Union[str, Sequence[str], None] = '67e3435e7a07'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# slides do carrossel do topo da página inicial, que antes ficavam escritos no código do frontend.
# Mesma proteção das outras tabelas (migration 56799f788354): RLS ligado e só a API acessa. Slide é
# conteúdo da loja, não histórico: a API também apaga (como a foto de produto, migration e7a1c4f20b31)
PAPEL = "api_casalorenzi"
POLITICA = f"{PAPEL}_acesso"

# os três slides que estavam no código, sem foto: as fotos são enviadas pelo interno (Catálogo > Destaques)
SLIDES_INICIAIS = [
    ("Linho", "Peças leves para a primavera e o verão.", "Ver as novidades", "/loja/produtos?ordem=novidades", 1),
    ("Alfaiataria", "Cortes retos que vão do trabalho ao fim de semana.", "Ver a alfaiataria",
     "/loja/produtos?busca=alfaiataria", 2),
    ("Retire grátis na loja", "Compre pelo site e retire em qualquer loja da rede, sem pagar frete.",
     "Saber como funciona", "/loja/ajuda#pedidos-e-entrega", 3),
]


def upgrade() -> None:
    """Upgrade schema."""
    destaque = op.create_table('destaque',
    sa.Column('id_destaque', sa.Integer(), nullable=False),
    sa.Column('titulo', sa.String(length=80), nullable=False),
    sa.Column('texto', sa.String(length=200), nullable=False),
    sa.Column('rotulo_botao', sa.String(length=40), nullable=False),
    sa.Column('link', sa.String(length=300), nullable=False),
    sa.Column('caminho_imagem', sa.String(length=500), nullable=True),
    sa.Column('ordem', sa.Integer(), nullable=False),
    sa.Column('ativo', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.CheckConstraint("link LIKE '/%' AND link NOT LIKE '//%'", name=op.f('ck_destaque_link_interno')),
    sa.CheckConstraint('ordem >= 1', name=op.f('ck_destaque_ordem_positiva')),
    sa.PrimaryKeyConstraint('id_destaque', name=op.f('pk_destaque'))
    )
    op.execute('ALTER TABLE "destaque" ENABLE ROW LEVEL SECURITY')
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON destaque TO {PAPEL};")
    op.execute(f"GRANT USAGE, SELECT ON SEQUENCE destaque_id_destaque_seq TO {PAPEL};")
    op.execute(f"CREATE POLICY {POLITICA} ON destaque FOR ALL TO {PAPEL} USING (true) WITH CHECK (true);")

    op.bulk_insert(destaque, [
        {"titulo": titulo, "texto": texto, "rotulo_botao": botao, "link": link, "ordem": ordem}
        for titulo, texto, botao, link, ordem in SLIDES_INICIAIS
    ])


def downgrade() -> None:
    """Downgrade schema."""
    # a regra de acesso e as permissões saem junto com a tabela
    op.drop_table('destaque')
