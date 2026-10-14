"""prazos automaticos e storage

Revision ID: 03aeb347f6cf
Revises: 56799f788354
Create Date: 2026-10-06

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '03aeb347f6cf'
down_revision: Union[str, Sequence[str], None] = '56799f788354'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PAPEL = "api_casalorenzi"
TAREFA = "cancela-vencidos"

# Prazos do case (seção 5): a reserva do checkout vale 15 minutos e a retirada, 7 dias depois de
# pronta. O pg_cron roda esta função a cada minuto, mesmo com a API dormindo no Render.
# Itens travados sempre por variante, na mesma ordem da API (evita deadlock).
CANCELA_VENCIDOS = """
    CREATE FUNCTION cancela_vencidos() RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
    DECLARE
        p record;
        item record;
        pago record;
    BEGIN
        -- reserva vencida: as peças voltam ao disponível e a cobrança pendente é recusada
        FOR p IN
            SELECT id_pedido, id_unidade FROM pedido
            WHERE status = 'aguardando_pagamento' AND reserva_expira_em < now()
            ORDER BY id_pedido FOR UPDATE SKIP LOCKED
        LOOP
            FOR item IN
                SELECT id_variante, sum(quantidade) AS quantidade FROM item_pedido
                WHERE id_pedido = p.id_pedido GROUP BY id_variante ORDER BY id_variante
            LOOP
                UPDATE estoque SET quantidade_reservada = quantidade_reservada - item.quantidade, atualizado_em = now()
                WHERE id_variante = item.id_variante AND id_unidade = p.id_unidade AND canal = 'online';
            END LOOP;
            UPDATE pagamento SET status = 'recusado', atualizado_em = now()
            WHERE id_pedido = p.id_pedido AND status = 'pendente';
            UPDATE pedido SET status = 'cancelado', motivo_cancelamento = 'reserva_vencida',
                cancelado_em = now(), atualizado_em = now()
            WHERE id_pedido = p.id_pedido;
        END LOOP;

        -- retirada vencida: estorno do que foi pago (mesmo método) e as peças voltam ao estoque online
        FOR p IN
            SELECT id_pedido, id_unidade FROM pedido
            WHERE status = 'pronto_para_retirada' AND pronto_retirada_em < now() - interval '7 days'
            ORDER BY id_pedido FOR UPDATE SKIP LOCKED
        LOOP
            FOR pago IN
                SELECT o.id_pagamento, o.metodo, o.valor - coalesce((
                    SELECT sum(e.valor) FROM pagamento e
                    WHERE e.id_pagamento_original = o.id_pagamento AND e.status <> 'recusado'
                ), 0) AS restante
                FROM pagamento o
                WHERE o.id_pedido = p.id_pedido AND o.tipo = 'pagamento' AND o.status = 'aprovado'
            LOOP
                IF pago.restante > 0 THEN
                    INSERT INTO pagamento (id_pedido, tipo, id_pagamento_original, metodo, valor, status)
                    VALUES (p.id_pedido, 'estorno', pago.id_pagamento, pago.metodo, pago.restante, 'aprovado');
                END IF;
            END LOOP;
            FOR item IN
                SELECT id_variante, sum(quantidade) AS quantidade FROM item_pedido
                WHERE id_pedido = p.id_pedido GROUP BY id_variante ORDER BY id_variante
            LOOP
                INSERT INTO movimentacao_estoque (id_variante, id_unidade, canal, tipo, quantidade, id_pedido)
                VALUES (item.id_variante, p.id_unidade, 'online', 'retorno_cancelamento', item.quantidade, p.id_pedido);
            END LOOP;
            UPDATE pedido SET status = 'cancelado', motivo_cancelamento = 'retirada_vencida',
                cancelado_em = now(), atualizado_em = now()
            WHERE id_pedido = p.id_pedido;
        END LOOP;
    END;
    $$;
"""

# fotos de produto ficam públicas; anexos de chamado e fotos de avaliação são privados e saem
# por link temporário (a foto de avaliação oculta sai do ar). Só o backend envia arquivos
BUCKETS = [
    ("produtos", "true", 5 * 1024 * 1024, "{image/jpeg,image/png,image/webp}"),
    ("anexos", "false", 10 * 1024 * 1024, "{image/jpeg,image/png,image/webp,application/pdf}"),
    ("avaliacoes", "false", 5 * 1024 * 1024, "{image/jpeg,image/png,image/webp}"),
]


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(CANCELA_VENCIDOS)
    op.execute("REVOKE ALL ON FUNCTION cancela_vencidos() FROM PUBLIC, anon, authenticated;")
    op.execute(f"GRANT EXECUTE ON FUNCTION cancela_vencidos() TO {PAPEL};")

    op.execute("CREATE EXTENSION IF NOT EXISTS pg_cron;")
    op.execute(f"SELECT cron.schedule('{TAREFA}', '* * * * *', 'SELECT cancela_vencidos()');")

    for nome, publico, limite, tipos in BUCKETS:
        op.execute(f"""
            INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
            VALUES ('{nome}', '{nome}', {publico}, {limite}, '{tipos}')
            ON CONFLICT (id) DO NOTHING;
        """)


def downgrade() -> None:
    """Downgrade schema."""
    # buckets com arquivos não são apagados pelo SQL: esvazie pelo painel do Supabase antes
    for nome, _, _, _ in reversed(BUCKETS):
        op.execute(f"DELETE FROM storage.buckets WHERE id = '{nome}';")
    op.execute(f"SELECT cron.unschedule('{TAREFA}');")
    op.execute("DROP FUNCTION cancela_vencidos();")
