"""trigger admin nao aceita excecoes

Revision ID: e78926429dcd
Revises: 9b52eb4fbf53
Create Date: 2026-10-05 18:08:23.520775

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e78926429dcd'
down_revision: Union[str, Sequence[str], None] = '9b52eb4fbf53'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ADR 0009: o Admin não aceita exceções de permissão, garantido no banco pelos três caminhos
def upgrade() -> None:
    """Upgrade schema."""
    # 1. recusa exceção para quem está no modelo Admin
    op.execute("""
        CREATE FUNCTION bloqueia_excecao_admin() RETURNS trigger
        LANGUAGE plpgsql SET search_path = public AS $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM usuario u
                JOIN modelo_acesso m ON m.id_modelo = u.id_modelo_acesso
                WHERE u.id_usuario = NEW.id_usuario AND m.eh_admin
            ) THEN
                RAISE EXCEPTION 'Admin não aceita exceções de permissão'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER trg_bloqueia_excecao_admin
        BEFORE INSERT OR UPDATE ON usuario_permissao_excecao
        FOR EACH ROW EXECUTE FUNCTION bloqueia_excecao_admin();
    """)

    # 2. quem passa para o modelo Admin perde as exceções que tinha
    op.execute("""
        CREATE FUNCTION limpa_excecoes_usuario_admin() RETURNS trigger
        LANGUAGE plpgsql SET search_path = public AS $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM modelo_acesso
                WHERE id_modelo = NEW.id_modelo_acesso AND eh_admin
            ) THEN
                DELETE FROM usuario_permissao_excecao WHERE id_usuario = NEW.id_usuario;
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER trg_limpa_excecoes_usuario_admin
        AFTER UPDATE OF id_modelo_acesso ON usuario
        FOR EACH ROW EXECUTE FUNCTION limpa_excecoes_usuario_admin();
    """)

    # 3. modelo que vira Admin: as pessoas ligadas a ele perdem as exceções
    op.execute("""
        CREATE FUNCTION limpa_excecoes_modelo_admin() RETURNS trigger
        LANGUAGE plpgsql SET search_path = public AS $$
        BEGIN
            IF NEW.eh_admin AND NOT OLD.eh_admin THEN
                DELETE FROM usuario_permissao_excecao e
                USING usuario u
                WHERE e.id_usuario = u.id_usuario AND u.id_modelo_acesso = NEW.id_modelo;
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER trg_limpa_excecoes_modelo_admin
        AFTER UPDATE OF eh_admin ON modelo_acesso
        FOR EACH ROW EXECUTE FUNCTION limpa_excecoes_modelo_admin();
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER trg_limpa_excecoes_modelo_admin ON modelo_acesso;")
    op.execute("DROP FUNCTION limpa_excecoes_modelo_admin();")
    op.execute("DROP TRIGGER trg_limpa_excecoes_usuario_admin ON usuario;")
    op.execute("DROP FUNCTION limpa_excecoes_usuario_admin();")
    op.execute("DROP TRIGGER trg_bloqueia_excecao_admin ON usuario_permissao_excecao;")
    op.execute("DROP FUNCTION bloqueia_excecao_admin();")
