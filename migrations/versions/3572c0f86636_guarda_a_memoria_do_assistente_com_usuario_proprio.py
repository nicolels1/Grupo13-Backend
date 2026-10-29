"""guarda a memoria do assistente com usuario proprio

Revision ID: 3572c0f86636
Revises: b5d8e3f1a7c2
Create Date: 2026-10-29 23:30:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '3572c0f86636'
down_revision: Union[str, Sequence[str], None] = 'b5d8e3f1a7c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Assistente do cliente (Entrega 4): a memória da conversa sai da RAM do n8n (Simple Memory) e vai para o banco
# (Postgres Chat Memory), para sobreviver a um reinício. O n8n fala direto com o banco, então ganha um usuário só
# dele, que enxerga apenas esta tabela, num schema próprio: fora do public, a API automática do Supabase não a
# expõe e o --autogenerate não a vê (sem model, ele proporia apagá-la).
# Usuário nasce sem poder entrar: a senha não vai para o git. Depois desta migration, no SQL Editor do Supabase:
#   ALTER ROLE n8n_casalorenzi WITH LOGIN PASSWORD '<senha forte>';
# e a credencial Postgres do n8n usa esse usuário (no pooler, n8n_casalorenzi.<ref do projeto>).
PAPEL = "n8n_casalorenzi"
SCHEMA = "memoria_assistente"
TABELA = f"{SCHEMA}.n8n_chat_histories"
TAREFA = "apaga-memoria-do-assistente"

# Fora de uma conversa a memória não serve: depois de 30 min parada a conversa recomeça e o n8n a apaga. Quem não
# volta deixaria o texto guardado para sempre, sem o mascaramento da mensagem_conversa; 1 dia cobre qualquer
# conversa em andamento
APAGA_MEMORIA_DO_ASSISTENTE = f"""
    CREATE OR REPLACE FUNCTION apaga_memoria_do_assistente() RETURNS void
    LANGUAGE sql SECURITY DEFINER SET search_path = {SCHEMA} AS $$
        DELETE FROM n8n_chat_histories WHERE created_at < now() - interval '1 day';
    $$;
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA};")
    op.execute(f"""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{PAPEL}') THEN
                CREATE ROLE {PAPEL} NOLOGIN;
            END IF;
        END $$;
    """)

    # a tabela foi criada à mão no public em 29/10: muda de lugar com o que já tiver; banco novo cria do zero.
    # As colunas são as que o nó do n8n cria (id, session_id, message), mais a data para a limpeza
    op.execute(f"ALTER TABLE IF EXISTS public.n8n_chat_histories SET SCHEMA {SCHEMA};")
    op.execute(f"ALTER INDEX IF EXISTS {SCHEMA}.n8n_chat_histories_session_idx RENAME TO ix_n8n_chat_histories_session_id;")
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {TABELA} (
            id         serial PRIMARY KEY,
            session_id varchar(255) NOT NULL,
            message    jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        );
    """)
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_n8n_chat_histories_session_id ON {TABELA} (session_id);")
    op.execute(f"ALTER TABLE {TABELA} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"CREATE POLICY {PAPEL}_acesso ON {TABELA} FOR ALL TO {PAPEL} USING (true) WITH CHECK (true);")

    # o nó roda CREATE TABLE IF NOT EXISTS a cada chamada, e o Postgres exige CREATE no schema mesmo com a tabela
    # pronta; no schema próprio isso não alcança nada do sistema. DELETE é o "apagar memória" da conversa nova
    op.execute(f"GRANT USAGE, CREATE ON SCHEMA {SCHEMA} TO {PAPEL};")
    op.execute(f"GRANT SELECT, INSERT, DELETE ON {TABELA} TO {PAPEL};")
    op.execute(f"GRANT USAGE ON SEQUENCE {TABELA}_id_seq TO {PAPEL};")
    # o nó recebe só o nome da tabela; o usuário já entra procurando no schema dele
    op.execute(f"ALTER ROLE {PAPEL} SET search_path = {SCHEMA};")

    op.execute(APAGA_MEMORIA_DO_ASSISTENTE)
    op.execute("REVOKE ALL ON FUNCTION apaga_memoria_do_assistente() FROM PUBLIC, anon, authenticated;")
    # 00h45 de Brasília
    op.execute(f"SELECT cron.schedule('{TAREFA}', '45 3 * * *', 'SELECT apaga_memoria_do_assistente()');")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(f"SELECT cron.unschedule('{TAREFA}');")
    op.execute("DROP FUNCTION apaga_memoria_do_assistente();")
    op.execute(f"DROP SCHEMA {SCHEMA} CASCADE;")
    op.execute(f"DROP ROLE {PAPEL};")
