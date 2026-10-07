"""troca e devolucao no balcao

Revision ID: 221981ca8429
Revises: bc431a82c8bf
Create Date: 2026-10-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '221981ca8429'
down_revision: Union[str, Sequence[str], None] = 'bc431a82c8bf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Troca e devolução no balcão, sem chamado (ADR 0015). O usuário restrito da API já lê permissao,
# insere movimentações e lê, insere e altera pagamento (colunas novas incluídas): nada a liberar

PERMISSAO = ("registrar_troca_devolucao", "Registrar troca e devolução no balcão")

# a função do pg_cron (migration 03aeb347f6cf) passa a marcar a origem do estorno da retirada vencida
CANCELA_VENCIDOS = """
    CREATE OR REPLACE FUNCTION cancela_vencidos() RETURNS void
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
INSERT_ANTIGO = """                    INSERT INTO pagamento (id_pedido, tipo, id_pagamento_original, metodo, valor, status)
                    VALUES (p.id_pedido, 'estorno', pago.id_pagamento, pago.metodo, pago.restante, 'aprovado');"""
INSERT_NOVO = """                    INSERT INTO pagamento (id_pedido, tipo, id_pagamento_original, metodo, valor, status, origem)
                    VALUES (p.id_pedido, 'estorno', pago.id_pagamento, pago.metodo, pago.restante, 'aprovado',
                            'cancelamento');"""

TRIGGER_IMUTAVEL = "trg_bloqueia_alteracao_movimentacao_estoque"
CHECKS_DO_ESTORNO = [
    ("ck_pagamento_origem_valida", "origem IN ('cancelamento', 'atendimento', 'balcao')"),
    ("ck_pagamento_origem_so_em_estorno", "(tipo = 'estorno') = (origem IS NOT NULL)"),
    ("ck_pagamento_estorno_balcao_exige_funcionario_e_unidade",
     "origem IS DISTINCT FROM 'balcao' OR (id_registrado_por IS NOT NULL AND id_unidade IS NOT NULL)"),
    ("ck_pagamento_estorno_atendimento_exige_chamado",
     "origem IS DISTINCT FROM 'atendimento' OR id_chamado IS NOT NULL"),
]
UMA_ORIGEM_ANTIGA = "num_nonnulls(id_pedido, id_transferencia, id_chamado) <= 1"
# troca e devolução podem ter pedido e chamado juntos (feitas pelo Atendimento)
UMA_ORIGEM_NOVA = (
    "num_nonnulls(id_pedido, id_transferencia, id_chamado) <= 1 "
    "OR (tipo IN ('devolucao', 'saida_troca') AND id_transferencia IS NULL)"
)
TROCA_DEVOLUCAO_COM_PEDIDO = (
    "tipo NOT IN ('devolucao', 'saida_troca') "
    "OR (id_pedido IS NOT NULL AND (id_chamado IS NOT NULL OR id_usuario IS NOT NULL))"
)


def upgrade() -> None:
    """Upgrade schema."""
    # 1. permissão nova; o Admin tem todas
    op.execute(f"INSERT INTO permissao (codigo, descricao) VALUES ('{PERMISSAO[0]}', '{PERMISSAO[1]}')")
    op.execute(f"""
        INSERT INTO modelo_permissao (id_modelo, id_permissao)
        SELECT m.id_modelo, p.id_permissao FROM modelo_acesso m, permissao p
        WHERE m.eh_admin AND p.codigo = '{PERMISSAO[0]}'
    """)

    # 2. estorno: quem registrou, onde e de onde veio. Os que já existem: com chamado vieram do
    # atendimento; sem chamado, de um cancelamento (equipe, pg_cron ou pagamento tardio)
    op.add_column('pagamento', sa.Column('id_registrado_por', sa.Uuid(), nullable=True))
    op.add_column('pagamento', sa.Column('id_unidade', sa.Integer(), nullable=True))
    op.add_column('pagamento', sa.Column('origem', sa.String(length=20), nullable=True))
    op.create_foreign_key(op.f('fk_pagamento_id_registrado_por_usuario'), 'pagamento', 'usuario',
                          ['id_registrado_por'], ['id_usuario'], ondelete='RESTRICT')
    op.create_foreign_key(op.f('fk_pagamento_id_unidade_unidade'), 'pagamento', 'unidade',
                          ['id_unidade'], ['id_unidade'], ondelete='RESTRICT')
    op.execute("""
        UPDATE pagamento SET origem = CASE WHEN id_chamado IS NOT NULL THEN 'atendimento' ELSE 'cancelamento' END
        WHERE tipo = 'estorno'
    """)
    for nome, regra in CHECKS_DO_ESTORNO:
        op.create_check_constraint(op.f(nome), 'pagamento', regra)
    op.execute(CANCELA_VENCIDOS)

    # 3. troca e devolução sempre ligadas ao pedido, com chamado ou funcionário. As que já existem
    # ganham o pedido do chamado; movimentação é imutável, então o trigger sai só durante o ajuste
    op.execute(f"ALTER TABLE movimentacao_estoque DISABLE TRIGGER {TRIGGER_IMUTAVEL}")
    op.execute("""
        UPDATE movimentacao_estoque m SET id_pedido = c.id_pedido
        FROM chamado c
        WHERE c.id_chamado = m.id_chamado AND m.tipo IN ('devolucao', 'saida_troca') AND m.id_pedido IS NULL
    """)
    op.execute(f"ALTER TABLE movimentacao_estoque ENABLE TRIGGER {TRIGGER_IMUTAVEL}")
    op.drop_constraint(op.f('ck_movimentacao_estoque_no_maximo_uma_origem'), 'movimentacao_estoque', type_='check')
    op.create_check_constraint(op.f('ck_movimentacao_estoque_no_maximo_uma_origem'), 'movimentacao_estoque',
                               UMA_ORIGEM_NOVA)
    op.create_check_constraint(op.f('ck_movimentacao_estoque_troca_devolucao_com_pedido'), 'movimentacao_estoque',
                               TROCA_DEVOLUCAO_COM_PEDIDO)


def downgrade() -> None:
    """Downgrade schema."""
    # a regra antiga só volta se nenhuma troca ou devolução tiver pedido e chamado juntos
    op.drop_constraint(op.f('ck_movimentacao_estoque_troca_devolucao_com_pedido'), 'movimentacao_estoque',
                       type_='check')
    op.drop_constraint(op.f('ck_movimentacao_estoque_no_maximo_uma_origem'), 'movimentacao_estoque', type_='check')
    op.create_check_constraint(op.f('ck_movimentacao_estoque_no_maximo_uma_origem'), 'movimentacao_estoque',
                               UMA_ORIGEM_ANTIGA)

    op.execute(CANCELA_VENCIDOS.replace(INSERT_NOVO, INSERT_ANTIGO))
    for nome, _ in reversed(CHECKS_DO_ESTORNO):
        op.drop_constraint(op.f(nome), 'pagamento', type_='check')
    op.drop_constraint(op.f('fk_pagamento_id_unidade_unidade'), 'pagamento', type_='foreignkey')
    op.drop_constraint(op.f('fk_pagamento_id_registrado_por_usuario'), 'pagamento', type_='foreignkey')
    op.drop_column('pagamento', 'origem')
    op.drop_column('pagamento', 'id_unidade')
    op.drop_column('pagamento', 'id_registrado_por')

    op.execute(f"""
        DELETE FROM modelo_permissao
        WHERE id_permissao = (SELECT id_permissao FROM permissao WHERE codigo = '{PERMISSAO[0]}')
    """)
    op.execute(f"DELETE FROM permissao WHERE codigo = '{PERMISSAO[0]}'")
