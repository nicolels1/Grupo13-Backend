"""protege saldo e gestao no banco

Revision ID: e08fd1b28bcf
Revises: b292949e2496
Create Date: 2026-10-05 23:12:50.670410

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e08fd1b28bcf'
down_revision: Union[str, Sequence[str], None] = 'b292949e2496'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# permissões da Gestão (seção 6 do case): só o Admin tem, e nunca por exceção
PERMISSOES_SO_ADMIN = "('gerenciar_contas', 'gerenciar_modelos_acesso', 'gerenciar_unidades')"

# regras que o case promete no banco: (nome, tabela, quando, corpo)
TRIGGERS_NOVOS = [
    # ADR 0005: o saldo só muda pelo aplica_movimentacao, que roda dentro do trigger da
    # movimentação (pg_trigger_depth 2); um INSERT ou UPDATE direto em estoque roda no nível 1
    ("bloqueia_saldo_direto", "estoque", "BEFORE INSERT OR UPDATE OF quantidade", """
        IF pg_trigger_depth() < 2 AND (
            (TG_OP = 'INSERT' AND NEW.quantidade <> 0)
            OR (TG_OP = 'UPDATE' AND NEW.quantidade <> OLD.quantidade)
        ) THEN
            RAISE EXCEPTION 'Saldo do estoque só muda por movimentação'
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    """),
    # permissão da Gestão só entra no modelo Admin
    ("gestao_so_no_admin", "modelo_permissao", "BEFORE INSERT OR UPDATE", f"""
        IF EXISTS (
            SELECT 1 FROM permissao WHERE id_permissao = NEW.id_permissao AND codigo IN {PERMISSOES_SO_ADMIN}
        ) AND NOT EXISTS (
            SELECT 1 FROM modelo_acesso WHERE id_modelo = NEW.id_modelo AND eh_admin
        ) THEN
            RAISE EXCEPTION 'Permissões da Gestão são só do Admin'
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    """),
    # permissão da Gestão não aceita exceção, nem de acrescentar nem de retirar
    ("gestao_sem_excecao", "usuario_permissao_excecao", "BEFORE INSERT OR UPDATE", f"""
        IF EXISTS (
            SELECT 1 FROM permissao WHERE id_permissao = NEW.id_permissao AND codigo IN {PERMISSOES_SO_ADMIN}
        ) THEN
            RAISE EXCEPTION 'Permissões da Gestão não aceitam exceção'
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    """),
]


def upgrade() -> None:
    """Upgrade schema."""
    # o usuário restrito do FastAPI não altera o saldo: o trigger do saldo roda com o dono da função
    op.execute("ALTER FUNCTION aplica_movimentacao() SECURITY DEFINER;")

    for nome, tabela, quando, corpo in TRIGGERS_NOVOS:
        op.execute(f"""
            CREATE FUNCTION {nome}() RETURNS trigger
            LANGUAGE plpgsql SET search_path = public AS $$
            BEGIN
            {corpo}
            END;
            $$;
        """)
        op.execute(f"CREATE TRIGGER trg_{nome} {quando} ON {tabela} FOR EACH ROW EXECUTE FUNCTION {nome}();")


def downgrade() -> None:
    """Downgrade schema."""
    for nome, tabela, _, _ in reversed(TRIGGERS_NOVOS):
        op.execute(f"DROP TRIGGER trg_{nome} ON {tabela};")
        op.execute(f"DROP FUNCTION {nome}();")

    op.execute("ALTER FUNCTION aplica_movimentacao() SECURITY INVOKER;")
