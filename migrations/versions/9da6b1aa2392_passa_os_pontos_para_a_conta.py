"""passa os pontos para a conta

Revision ID: 9da6b1aa2392
Revises: 380e5e8b5f0a
Create Date: 2026-10-21 00:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9da6b1aa2392'
down_revision: Union[str, Sequence[str], None] = '380e5e8b5f0a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Pontos do cliente sem conta (P2, P5): quando o cadastro grava para qual conta ele passou (ADR 0016), o saldo e os
# lotes vão para a conta, com a época e a validade de cada ponto. Os movimentos ficam com o dono original (o extrato
# da conta mostra os dois). Se a conta já tem saldo na marca, os saldos se somam e a categoria fica a maior

PASSA_PONTOS_PARA_A_CONTA = """
    CREATE FUNCTION passa_pontos_para_a_conta() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
    DECLARE
        ano integer := extract(year FROM now() AT TIME ZONE 'America/Sao_Paulo');
        ordem text[] := ARRAY['bronze', 'prata', 'ouro'];
        origem saldo_pontos;
        destino saldo_pontos;
        maior boolean;
    BEGIN
        FOR origem IN
            SELECT * FROM saldo_pontos WHERE id_cliente_sem_conta = NEW.id_cliente_sem_conta ORDER BY marca FOR UPDATE
        LOOP
            SELECT * INTO destino FROM saldo_pontos
            WHERE id_cliente = NEW.id_usuario AND marca = origem.marca FOR UPDATE;
            IF destino.id_saldo IS NULL THEN
                UPDATE saldo_pontos SET id_cliente = NEW.id_usuario, id_cliente_sem_conta = NULL, atualizado_em = now()
                WHERE id_saldo = origem.id_saldo;
            ELSE
                -- os dois no mesmo ano, para somar os pontos ganhos no ano
                PERFORM vira_ano_pontos(origem.id_saldo, ano);
                PERFORM vira_ano_pontos(destino.id_saldo, ano);
                SELECT * INTO origem FROM saldo_pontos WHERE id_saldo = origem.id_saldo;
                SELECT * INTO destino FROM saldo_pontos WHERE id_saldo = destino.id_saldo;
                maior := array_position(ordem, origem.categoria) > array_position(ordem, destino.categoria);
                DELETE FROM saldo_pontos WHERE id_saldo = origem.id_saldo;
                UPDATE saldo_pontos SET
                    saldo = destino.saldo + origem.saldo,
                    ganhos_no_ano = destino.ganhos_no_ano + origem.ganhos_no_ano,
                    categoria = CASE WHEN maior THEN origem.categoria ELSE destino.categoria END,
                    categoria_desde = CASE WHEN maior THEN origem.categoria_desde ELSE destino.categoria_desde END,
                    atualizado_em = now()
                WHERE id_saldo = destino.id_saldo;
            END IF;
            UPDATE lote_pontos SET id_cliente = NEW.id_usuario, id_cliente_sem_conta = NULL
            WHERE id_cliente_sem_conta = NEW.id_cliente_sem_conta AND marca = origem.marca;
        END LOOP;
        RETURN NEW;
    END;
    $$;
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(PASSA_PONTOS_PARA_A_CONTA)
    op.execute("REVOKE ALL ON FUNCTION passa_pontos_para_a_conta() FROM PUBLIC, anon, authenticated;")
    op.execute("""
        CREATE TRIGGER trg_passa_pontos_para_a_conta
        AFTER UPDATE OF id_usuario ON cliente_sem_conta
        FOR EACH ROW WHEN (OLD.id_usuario IS NULL AND NEW.id_usuario IS NOT NULL)
        EXECUTE FUNCTION passa_pontos_para_a_conta();
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER trg_passa_pontos_para_a_conta ON cliente_sem_conta;")
    op.execute("DROP FUNCTION passa_pontos_para_a_conta();")
