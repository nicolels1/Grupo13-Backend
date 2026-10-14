"""triggers de estoque e registros imutaveis

Revision ID: e3f070d83fd6
Revises: a6125052c5a1
Create Date: 2026-10-05 20:51:37.884444

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e3f070d83fd6'
down_revision: Union[str, Sequence[str], None] = 'a6125052c5a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# tabelas de histórico: nunca editadas nem apagadas
TABELAS_IMUTAVEIS = ["movimentacao_estoque", "historico_preco", "historico_chamado"]


def upgrade() -> None:
    """Upgrade schema."""
    # ADR 0005: cada movimentação atualiza o saldo, criando a linha de ESTOQUE se não existir
    op.execute("""
        CREATE FUNCTION aplica_movimentacao() RETURNS trigger
        LANGUAGE plpgsql SET search_path = public AS $$
        DECLARE
            atual integer;
        BEGIN
            -- o CD só tem o canal online
            IF NEW.canal = 'loja_fisica' AND EXISTS (
                SELECT 1 FROM unidade WHERE id_unidade = NEW.id_unidade AND tipo = 'cd'
            ) THEN
                RAISE EXCEPTION 'CD não tem estoque de loja física'
                    USING ERRCODE = 'check_violation';
            END IF;

            -- garante a linha (com saldo 0) e a trava, para duas movimentações
            -- simultâneas não lerem o mesmo saldo
            INSERT INTO estoque (id_variante, id_unidade, canal)
            VALUES (NEW.id_variante, NEW.id_unidade, NEW.canal)
            ON CONFLICT (id_variante, id_unidade, canal) DO NOTHING;

            SELECT quantidade INTO atual FROM estoque
            WHERE id_variante = NEW.id_variante AND id_unidade = NEW.id_unidade AND canal = NEW.canal
            FOR UPDATE;
            IF atual + NEW.quantidade < 0 THEN
                RAISE EXCEPTION 'Estoque insuficiente: saldo %, movimentação %', atual, NEW.quantidade
                    USING ERRCODE = 'check_violation';
            END IF;

            UPDATE estoque SET quantidade = atual + NEW.quantidade, atualizado_em = now()
            WHERE id_variante = NEW.id_variante AND id_unidade = NEW.id_unidade AND canal = NEW.canal;
            RETURN NEW;
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER trg_aplica_movimentacao
        AFTER INSERT ON movimentacao_estoque
        FOR EACH ROW EXECUTE FUNCTION aplica_movimentacao();
    """)

    op.execute("""
        CREATE FUNCTION bloqueia_alteracao() RETURNS trigger
        LANGUAGE plpgsql SET search_path = public AS $$
        BEGIN
            RAISE EXCEPTION 'Registros de % não podem ser alterados nem apagados', TG_TABLE_NAME
                USING ERRCODE = 'check_violation';
        END;
        $$;
    """)
    for tabela in TABELAS_IMUTAVEIS:
        op.execute(f"""
            CREATE TRIGGER trg_bloqueia_alteracao_{tabela}
            BEFORE UPDATE OR DELETE ON {tabela}
            FOR EACH ROW EXECUTE FUNCTION bloqueia_alteracao();
        """)


def downgrade() -> None:
    """Downgrade schema."""
    for tabela in reversed(TABELAS_IMUTAVEIS):
        op.execute(f"DROP TRIGGER trg_bloqueia_alteracao_{tabela} ON {tabela};")
    op.execute("DROP FUNCTION bloqueia_alteracao();")
    op.execute("DROP TRIGGER trg_aplica_movimentacao ON movimentacao_estoque;")
    op.execute("DROP FUNCTION aplica_movimentacao();")
