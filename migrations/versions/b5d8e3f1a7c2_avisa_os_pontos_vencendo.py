"""avisa os pontos vencendo

Revision ID: b5d8e3f1a7c2
Revises: b7e4c2a91f3d
Create Date: 2026-10-29 23:00:00.000000

"""
import os
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from src.config import settings


# revision identifiers, used by Alembic.
revision: str = 'b5d8e3f1a7c2'
down_revision: Union[str, Sequence[str], None] = 'b7e4c2a91f3d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Assistente do cliente (Entrega 4): e-mail de pontos vencendo para quem aceitou o lembrete. O backend no Render
# não tem relógio (dorme parado), então o pg_cron chama a rota do aviso uma vez por dia pelo pg_net. A chave do
# assistente e o endereço da API vão para o cofre do Supabase (vault), lidos do .env na hora de aplicar: nenhum
# segredo fica no código. A tabela guarda os lotes já avisados, para não repetir o e-mail

PAPEL = "api_casalorenzi"
TAREFA = "avisa-pontos-vencendo"
# o API_URL vem do .env, não do padrão local: o pg_cron chama o Render, nunca o localhost
SEGREDOS = [("api_url", (os.getenv("API_URL") or "").rstrip("/")), ("assistente_chave", settings.ASSISTENTE_CHAVE)]

DISPARA_AVISO = """
    CREATE OR REPLACE FUNCTION dispara_aviso_pontos_vencendo() RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
    DECLARE
        url text;
        chave text;
    BEGIN
        SELECT decrypted_secret INTO url FROM vault.decrypted_secrets WHERE name = 'api_url';
        SELECT decrypted_secret INTO chave FROM vault.decrypted_secrets WHERE name = 'assistente_chave';
        IF url IS NULL OR chave IS NULL THEN
            RAISE WARNING 'aviso de pontos vencendo sem api_url ou assistente_chave no vault';
            RETURN;
        END IF;
        -- o Render grátis pode estar dormindo: até 2 min para acordar e responder
        PERFORM net.http_post(
            url := url || '/assistente/avisos/pontos-vencendo',
            headers := jsonb_build_object('X-Assistente-Chave', chave, 'Content-Type', 'application/json'),
            body := '{}'::jsonb,
            timeout_milliseconds := 120000
        );
    END;
    $$;
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('aviso_pontos_vencendo',
    sa.Column('id_movimento', sa.Integer(), nullable=False),
    sa.Column('avisado_em', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['id_movimento'], ['lote_pontos.id_movimento'], name=op.f('fk_aviso_pontos_vencendo_id_movimento_lote_pontos'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id_movimento', name=op.f('pk_aviso_pontos_vencendo'))
    )
    op.execute('ALTER TABLE "aviso_pontos_vencendo" ENABLE ROW LEVEL SECURITY')
    op.execute(f'CREATE POLICY api_casalorenzi_acesso ON "aviso_pontos_vencendo" FOR ALL TO {PAPEL} USING (true) WITH CHECK (true)')
    op.execute(f'GRANT SELECT, INSERT ON "aviso_pontos_vencendo" TO {PAPEL}')

    op.execute("CREATE EXTENSION IF NOT EXISTS pg_net;")
    for nome, valor in SEGREDOS:
        if not valor:
            raise RuntimeError(f"Configure {nome.upper()} no .env antes de aplicar: o pg_cron precisa dele no vault")
        op.execute(sa.text("DELETE FROM vault.secrets WHERE name = :nome").bindparams(nome=nome))
        op.execute(sa.text("SELECT vault.create_secret(:valor, :nome)").bindparams(valor=valor, nome=nome))
    op.execute(DISPARA_AVISO)
    op.execute("REVOKE ALL ON FUNCTION dispara_aviso_pontos_vencendo() FROM PUBLIC, anon, authenticated;")
    # 09h de Brasília
    op.execute(f"SELECT cron.schedule('{TAREFA}', '0 12 * * *', 'SELECT dispara_aviso_pontos_vencendo()');")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(f"SELECT cron.unschedule('{TAREFA}');")
    op.execute("DROP FUNCTION dispara_aviso_pontos_vencendo();")
    for nome, _ in SEGREDOS:
        op.execute(sa.text("DELETE FROM vault.secrets WHERE name = :nome").bindparams(nome=nome))
    op.drop_table('aviso_pontos_vencendo')
