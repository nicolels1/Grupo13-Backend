"""cria horario das lojas

Revision ID: a41c7e2b9d05
Revises: 8834803e1dc8
Create Date: 2026-10-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a41c7e2b9d05'
down_revision: Union[str, Sequence[str], None] = '8834803e1dc8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Horário das lojas, para o assistente do cliente responder "que horas fecha?" sem IA: o horário de
# cada dia da semana e as exceções de uma data (feriado, horário especial do shopping).
# Mesma proteção das outras tabelas (migration 56799f788354): RLS ligado e só a API acessa
PAPEL = "api_casalorenzi"
POLITICA = f"{PAPEL}_acesso"
TABELAS = ("horario_unidade", "excecao_horario")


def upgrade() -> None:
    op.create_table('horario_unidade',
    sa.Column('id_unidade', sa.Integer(), nullable=False),
    sa.Column('dia_semana', sa.SmallInteger(), nullable=False),
    sa.Column('abre', sa.Time(), nullable=False),
    sa.Column('fecha', sa.Time(), nullable=False),
    sa.CheckConstraint('dia_semana BETWEEN 0 AND 6', name=op.f('ck_horario_unidade_dia_semana_valido')),
    sa.CheckConstraint('abre < fecha', name=op.f('ck_horario_unidade_abre_antes_de_fechar')),
    sa.ForeignKeyConstraint(['id_unidade'], ['unidade.id_unidade'], name=op.f('fk_horario_unidade_id_unidade_unidade'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id_unidade', 'dia_semana', name=op.f('pk_horario_unidade'))
    )
    op.create_table('excecao_horario',
    sa.Column('id_unidade', sa.Integer(), nullable=False),
    sa.Column('data', sa.Date(), nullable=False),
    sa.Column('abre', sa.Time(), nullable=True),
    sa.Column('fecha', sa.Time(), nullable=True),
    sa.Column('motivo', sa.String(length=100), nullable=False),
    sa.CheckConstraint('(abre IS NULL) = (fecha IS NULL)', name=op.f('ck_excecao_horario_abre_e_fecha_juntos')),
    sa.CheckConstraint('abre IS NULL OR abre < fecha', name=op.f('ck_excecao_horario_excecao_abre_antes_de_fechar')),
    sa.ForeignKeyConstraint(['id_unidade'], ['unidade.id_unidade'], name=op.f('fk_excecao_horario_id_unidade_unidade'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id_unidade', 'data', name=op.f('pk_excecao_horario'))
    )
    for tabela in TABELAS:
        op.execute(f'ALTER TABLE "{tabela}" ENABLE ROW LEVEL SECURITY')
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tabela} TO {PAPEL};")
        op.execute(f"CREATE POLICY {POLITICA} ON {tabela} FOR ALL TO {PAPEL} USING (true) WITH CHECK (true);")


def downgrade() -> None:
    op.drop_table('excecao_horario')
    op.drop_table('horario_unidade')
