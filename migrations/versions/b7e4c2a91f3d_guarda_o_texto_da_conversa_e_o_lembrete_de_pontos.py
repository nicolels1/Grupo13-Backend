"""guarda o texto da conversa e o lembrete de pontos

Revision ID: b7e4c2a91f3d
Revises: d70099d5cb80
Create Date: 2026-10-29 20:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b7e4c2a91f3d'
down_revision: Union[str, Sequence[str], None] = 'd70099d5cb80'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Assistente do cliente (Entrega 4): o texto das conversas fica 90 dias, para revisar onde o assistente falhou;
# o pg_cron apaga o resto todo dia (a API só grava e lê, sem DELETE). A conversa guarda quando começou, para o
# texto anônimo passar para a conta quando o cliente se confirma. A conta ganha o aceite do lembrete de pontos

PAPEL = "api_casalorenzi"
TAREFA = "apaga-texto-da-conversa"

APAGA_TEXTO_DA_CONVERSA = """
    CREATE OR REPLACE FUNCTION apaga_texto_da_conversa() RETURNS void
    LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
        DELETE FROM mensagem_conversa WHERE criado_em < now() - interval '90 days';
    $$;
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('conversa_canal', sa.Column('conversa_iniciada_em', sa.DateTime(timezone=True), nullable=True))
    op.add_column('usuario', sa.Column('lembrete_pontos_aceito_em', sa.DateTime(timezone=True), nullable=True))
    op.add_column('usuario', sa.Column('lembrete_pontos_oferecido_em', sa.DateTime(timezone=True), nullable=True))

    op.create_table('mensagem_conversa',
    sa.Column('id_mensagem', sa.Integer(), nullable=False),
    sa.Column('canal', sa.String(length=10), nullable=False),
    sa.Column('id_externo', sa.String(length=100), nullable=False),
    sa.Column('id_cliente', sa.Uuid(), nullable=True),
    sa.Column('id_cliente_sem_conta', sa.Integer(), nullable=True),
    sa.Column('papel', sa.String(length=10), nullable=False),
    sa.Column('texto', sa.Text(), nullable=False),
    sa.Column('criado_em', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("papel IN ('cliente', 'assistente')", name=op.f('ck_mensagem_conversa_papel_valido')),
    sa.CheckConstraint('id_cliente IS NULL OR id_cliente_sem_conta IS NULL', name=op.f('ck_mensagem_conversa_um_dono_so')),
    sa.ForeignKeyConstraint(['canal', 'id_externo'], ['conversa_canal.canal', 'conversa_canal.id_externo'], name='fk_mensagem_conversa_conversa_canal', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['id_cliente'], ['usuario.id_usuario'], name=op.f('fk_mensagem_conversa_id_cliente_usuario'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['id_cliente_sem_conta'], ['cliente_sem_conta.id_cliente_sem_conta'], name=op.f('fk_mensagem_conversa_id_cliente_sem_conta_cliente_sem_conta'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id_mensagem', name=op.f('pk_mensagem_conversa'))
    )
    op.execute('ALTER TABLE "mensagem_conversa" ENABLE ROW LEVEL SECURITY')
    op.execute(f'CREATE POLICY api_casalorenzi_acesso ON "mensagem_conversa" FOR ALL TO {PAPEL} USING (true) WITH CHECK (true)')
    op.execute(f'GRANT SELECT, INSERT, UPDATE ON "mensagem_conversa" TO {PAPEL}')
    op.execute(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {PAPEL}')
    op.create_index('ix_mensagem_conversa_conversa', 'mensagem_conversa', ['canal', 'id_externo', 'criado_em'], unique=False)
    op.create_index('ix_mensagem_conversa_criado_em', 'mensagem_conversa', ['criado_em'], unique=False)

    op.execute(APAGA_TEXTO_DA_CONVERSA)
    op.execute("REVOKE ALL ON FUNCTION apaga_texto_da_conversa() FROM PUBLIC, anon, authenticated;")
    # 00h30 de Brasília
    op.execute(f"SELECT cron.schedule('{TAREFA}', '30 3 * * *', 'SELECT apaga_texto_da_conversa()');")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(f"SELECT cron.unschedule('{TAREFA}');")
    op.execute("DROP FUNCTION apaga_texto_da_conversa();")
    op.drop_index('ix_mensagem_conversa_criado_em', table_name='mensagem_conversa')
    op.drop_index('ix_mensagem_conversa_conversa', table_name='mensagem_conversa')
    op.drop_table('mensagem_conversa')
    op.drop_column('usuario', 'lembrete_pontos_oferecido_em')
    op.drop_column('usuario', 'lembrete_pontos_aceito_em')
    op.drop_column('conversa_canal', 'conversa_iniciada_em')
