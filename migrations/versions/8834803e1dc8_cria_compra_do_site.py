"""cria compra do site

Revision ID: 8834803e1dc8
Revises: c7d2e9a41f60
Create Date: 2026-10-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8834803e1dc8'
down_revision: Union[str, Sequence[str], None] = 'c7d2e9a41f60'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Compra do site (ADR 0017): o cliente fecha o carrinho das duas marcas, paga uma vez só e cada marca sai
# num pedido próprio. A compra guarda a cobrança única do gateway simulado; na aprovação cada pedido ganha o
# seu lançamento em pagamento, então faturamento, caixa e os triggers de pontos continuam por pedido.
# Pedidos da loja, da migração e do checkout de um pedido só ficam sem compra.
# Mesma proteção das outras tabelas (migration 56799f788354): RLS ligado e só a API acessa, sem apagar
PAPEL = "api_casalorenzi"
POLITICA = f"{PAPEL}_acesso"

# a função do pg_cron (versão da migration 221981ca8429) passa a vencer a compra junto com os pedidos dela.
# Trava a compra antes dos pedidos, na mesma ordem da API: a compra que a API está pagando agora fica para o
# próximo minuto, com os pedidos dela, e nunca vence só um pedido de uma compra
DECLARA_COMPRAS = """
        compras int[];"""
TRAVA_COMPRAS = """
        -- compras do site vencidas, travadas antes dos pedidos delas
        SELECT coalesce(array_agg(id_compra), '{}') INTO compras FROM (
            SELECT id_compra FROM compra
            WHERE status = 'aguardando_pagamento' AND reserva_expira_em < now()
            ORDER BY id_compra FOR UPDATE SKIP LOCKED
        ) vencidas;
"""
SO_COMPRA_TRAVADA = """
                AND NOT EXISTS (
                    SELECT 1 FROM compra c
                    WHERE c.id_compra = pedido.id_compra AND c.status = 'aguardando_pagamento'
                        AND NOT c.id_compra = ANY(compras)
                )"""
VENCE_COMPRAS = """
        UPDATE compra SET status = 'cancelado', cancelado_em = now(), atualizado_em = now(),
            status_cobranca = CASE WHEN status_cobranca = 'pendente' THEN 'recusado' ELSE status_cobranca END
        WHERE id_compra = ANY(compras);
"""
CANCELA_VENCIDOS = f"""
    CREATE OR REPLACE FUNCTION cancela_vencidos() RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
    DECLARE
        p record;
        item record;
        pago record;{DECLARA_COMPRAS}
    BEGIN{TRAVA_COMPRAS}
        -- reserva vencida: as peças voltam ao disponível e a cobrança pendente é recusada
        FOR p IN
            SELECT id_pedido, id_unidade FROM pedido
            WHERE status = 'aguardando_pagamento' AND reserva_expira_em < now(){SO_COMPRA_TRAVADA}
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
{VENCE_COMPRAS}
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
                    INSERT INTO pagamento (id_pedido, tipo, id_pagamento_original, metodo, valor, status, origem)
                    VALUES (p.id_pedido, 'estorno', pago.id_pagamento, pago.metodo, pago.restante, 'aprovado',
                            'cancelamento');
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
# a versão anterior é esta sem os trechos da compra
CANCELA_VENCIDOS_ANTIGA = CANCELA_VENCIDOS
for _trecho in (DECLARA_COMPRAS, TRAVA_COMPRAS, SO_COMPRA_TRAVADA, VENCE_COMPRAS):
    CANCELA_VENCIDOS_ANTIGA = CANCELA_VENCIDOS_ANTIGA.replace(_trecho, "")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('compra',
    sa.Column('id_compra', sa.Integer(), nullable=False),
    sa.Column('codigo_compra', sa.String(length=20), nullable=False),
    sa.Column('id_cliente', sa.Uuid(), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('valor_total', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('reserva_expira_em', sa.DateTime(timezone=True), nullable=False),
    sa.Column('metodo', sa.String(length=20), nullable=True),
    sa.Column('id_transacao_gateway', sa.String(length=100), nullable=True),
    sa.Column('status_cobranca', sa.String(length=20), nullable=True),
    sa.Column('pago_em', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cancelado_em', sa.DateTime(timezone=True), nullable=True),
    sa.Column('criado_em', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('atualizado_em', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('aguardando_pagamento', 'pago', 'cancelado')",
                       name=op.f('ck_compra_status_valido')),
    sa.CheckConstraint("metodo IN ('pix', 'cartao_credito', 'cartao_debito')", name=op.f('ck_compra_metodo_valido')),
    sa.CheckConstraint("status_cobranca IN ('pendente', 'aprovado', 'recusado')",
                       name=op.f('ck_compra_status_cobranca_valido')),
    sa.CheckConstraint('num_nonnulls(metodo, id_transacao_gateway, status_cobranca) IN (0, 3)',
                       name=op.f('ck_compra_cobranca_completa')),
    sa.CheckConstraint(
        "(status = 'pago') = (status_cobranca IS NOT DISTINCT FROM 'aprovado' AND pago_em IS NOT NULL)",
        name=op.f('ck_compra_pago_exige_cobranca_aprovada')),
    sa.CheckConstraint("(status = 'cancelado') = (cancelado_em IS NOT NULL)",
                       name=op.f('ck_compra_cancelado_tem_data')),
    sa.CheckConstraint('valor_total >= 0', name=op.f('ck_compra_valor_nao_negativo')),
    sa.ForeignKeyConstraint(['id_cliente'], ['usuario.id_usuario'], name=op.f('fk_compra_id_cliente_usuario'),
                            ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id_compra', name=op.f('pk_compra')),
    sa.UniqueConstraint('codigo_compra', name=op.f('uq_compra_codigo_compra')),
    sa.UniqueConstraint('id_transacao_gateway', name=op.f('uq_compra_id_transacao_gateway'))
    )
    op.create_index('ix_compra_id_cliente', 'compra', ['id_cliente'], unique=False)
    op.execute('ALTER TABLE "compra" ENABLE ROW LEVEL SECURITY')
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON compra TO {PAPEL};")
    op.execute(f"GRANT USAGE, SELECT ON SEQUENCE compra_id_compra_seq TO {PAPEL};")
    op.execute(f"CREATE POLICY {POLITICA} ON compra FOR ALL TO {PAPEL} USING (true) WITH CHECK (true);")

    op.add_column('pedido', sa.Column('id_compra', sa.Integer(), nullable=True))
    op.create_index('ix_pedido_id_compra', 'pedido', ['id_compra'], unique=False)
    op.create_foreign_key(op.f('fk_pedido_id_compra_compra'), 'pedido', 'compra', ['id_compra'], ['id_compra'],
                          ondelete='RESTRICT')
    op.execute(CANCELA_VENCIDOS)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(CANCELA_VENCIDOS_ANTIGA)
    op.drop_constraint(op.f('fk_pedido_id_compra_compra'), 'pedido', type_='foreignkey')
    op.drop_index('ix_pedido_id_compra', table_name='pedido')
    op.drop_column('pedido', 'id_compra')
    # a regra de acesso e as permissões saem junto com a tabela
    op.drop_index('ix_compra_id_cliente', table_name='compra')
    op.drop_table('compra')
