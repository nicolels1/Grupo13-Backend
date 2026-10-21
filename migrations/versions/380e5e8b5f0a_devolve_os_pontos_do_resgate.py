"""devolve os pontos do resgate

Revision ID: 380e5e8b5f0a
Revises: 5806135c2561
Create Date: 2026-10-21 00:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '380e5e8b5f0a'
down_revision: Union[str, Sequence[str], None] = '5806135c2561'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Resgate de pontos (P10): os pontos usados como desconto voltam quando o pedido é cancelado (pelo cliente,
# pela equipe ou pelo pg_cron, na reserva ou na retirada vencida) ou devolvido inteiro. Na devolução parcial,
# o desconto fica na compra. Os pontos voltam para os lotes de onde saíram, com a época e a validade deles
# (trigger aplica_movimento_pontos)

DEVOLVE_PONTOS_DO_RESGATE = """
    CREATE FUNCTION devolve_pontos_do_resgate() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
    DECLARE
        resgate record;
    BEGIN
        IF NOT ((NEW.status = 'cancelado' AND OLD.status <> 'cancelado')
                OR (NEW.devolucao = 'total' AND OLD.devolucao <> 'total')) THEN
            RETURN NEW;
        END IF;
        -- o que ainda não voltou de cada resgate (cancelar depois de devolver não devolve duas vezes)
        FOR resgate IN
            SELECT r.id_movimento, r.marca, -r.pontos - coalesce((
                SELECT sum(e.pontos) FROM movimento_pontos e
                WHERE e.tipo = 'estorno' AND e.id_movimento_original = r.id_movimento), 0) AS falta
            FROM movimento_pontos r
            WHERE r.id_pedido = NEW.id_pedido AND r.tipo = 'resgate'
        LOOP
            IF resgate.falta > 0 THEN
                INSERT INTO movimento_pontos (id_cliente, id_cliente_sem_conta, marca, tipo, pontos, origem, id_pedido,
                                              id_movimento_original, motivo)
                VALUES (NEW.id_cliente, NEW.id_cliente_sem_conta, resgate.marca, 'estorno', resgate.falta, 'plataforma',
                        NEW.id_pedido, resgate.id_movimento,
                        CASE WHEN NEW.status = 'cancelado' THEN 'Pedido cancelado' ELSE 'Pedido devolvido' END);
            END IF;
        END LOOP;
        RETURN NEW;
    END;
    $$;
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(DEVOLVE_PONTOS_DO_RESGATE)
    op.execute("REVOKE ALL ON FUNCTION devolve_pontos_do_resgate() FROM PUBLIC, anon, authenticated;")
    op.execute("""
        CREATE TRIGGER trg_devolve_pontos_do_resgate
        AFTER UPDATE OF status, devolucao ON pedido
        FOR EACH ROW EXECUTE FUNCTION devolve_pontos_do_resgate();
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER trg_devolve_pontos_do_resgate ON pedido;")
    op.execute("DROP FUNCTION devolve_pontos_do_resgate();")
