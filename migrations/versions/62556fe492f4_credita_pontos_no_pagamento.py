"""credita pontos no pagamento

Revision ID: 62556fe492f4
Revises: a6e484a43c9d
Create Date: 2026-10-20 23:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '62556fe492f4'
down_revision: Union[str, Sequence[str], None] = 'a6e484a43c9d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Pontos no pagamento (P5, P10): o pedido ganha pontos quando os pagamentos aprovados cobrem o total ("ponto
# só é creditado depois do pagamento aprovado", regra do Vulto+), e o estorno de dinheiro tira a parte
# proporcional dos pontos. Fica no banco para valer em todo caminho, inclusive no cancelamento da retirada
# vencida pelo pg_cron. O pagamento migrado (origem migracao, P7) não credita nem estorna: os pontos da
# Vulto entram pelos movimentos da carga. Pedido sem cliente (só com o CPF na nota) não ganha pontos

FUNCOES = [
    # crédito: pedido pago de quem tem conta ou é cliente sem conta. Pontos = (itens − desconto) × pontos por
    # real, em dobro no online e +50% no Ouro, pela regra da marca da unidade no dia do pagamento
    ("credita_pedido_pago()", """
        CREATE FUNCTION credita_pedido_pago() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
        DECLARE
            p pedido;
            marca_pedido text;
            r regra_fidelidade;
            s saldo_pontos;
            pontos_ganhos integer;
            ano integer := extract(year FROM now() AT TIME ZONE 'America/Sao_Paulo');
        BEGIN
            IF NEW.tipo <> 'pagamento' OR NEW.status <> 'aprovado' OR NEW.origem IS NOT NULL
                OR (TG_OP = 'UPDATE' AND OLD.status = 'aprovado') THEN
                RETURN NEW;
            END IF;
            SELECT * INTO p FROM pedido WHERE id_pedido = NEW.id_pedido;
            IF p.status = 'cancelado' OR (p.id_cliente IS NULL AND p.id_cliente_sem_conta IS NULL)
                OR EXISTS (SELECT 1 FROM movimento_pontos WHERE id_pedido = p.id_pedido AND tipo = 'credito')
                OR (SELECT sum(valor) FROM pagamento WHERE id_pedido = p.id_pedido AND tipo = 'pagamento'
                    AND status = 'aprovado') < p.valor_total THEN
                RETURN NEW;
            END IF;
            SELECT u.marca INTO marca_pedido FROM unidade u WHERE u.id_unidade = p.id_unidade;
            r := regra_vigente(marca_pedido, now());
            IF r.id_regra IS NULL THEN
                RETURN NEW;  -- marca sem programa de fidelidade
            END IF;

            -- a categoria do bônus é a de hoje: vira o ano antes, se o cliente ainda não teve movimento no ano
            SELECT * INTO s FROM saldo_pontos WHERE marca = marca_pedido
                AND id_cliente IS NOT DISTINCT FROM p.id_cliente
                AND id_cliente_sem_conta IS NOT DISTINCT FROM p.id_cliente_sem_conta;
            IF s.id_saldo IS NOT NULL AND s.ano_referencia < ano THEN
                PERFORM vira_ano_pontos(s.id_saldo, ano);
                SELECT * INTO s FROM saldo_pontos WHERE id_saldo = s.id_saldo;
            END IF;

            pontos_ganhos := floor((p.valor_total - p.valor_frete) * r.pontos_por_real
                * CASE WHEN p.canal = 'online' THEN r.multiplicador_online ELSE 1 END
                * CASE WHEN s.categoria = 'ouro' THEN 1 + r.bonus_ouro ELSE 1 END);
            IF pontos_ganhos > 0 THEN
                INSERT INTO movimento_pontos (id_cliente, id_cliente_sem_conta, marca, tipo, pontos, origem, id_pedido)
                VALUES (p.id_cliente, p.id_cliente_sem_conta, marca_pedido, 'credito', pontos_ganhos, 'plataforma',
                        p.id_pedido);
            END IF;
            RETURN NEW;
        END;
        $$;
    """),
    # estorno de dinheiro (cancelamento, devolução, atendimento ou balcão): tira dos pontos do pedido a mesma
    # proporção do total que voltou para o cliente, somando os estornos (assim a soma das partes fecha com o
    # total). Ponto já gasto não deixa o saldo negativo: o estorno fica limitado ao saldo
    ("estorna_pontos_do_pagamento()", """
        CREATE FUNCTION estorna_pontos_do_pagamento() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
        DECLARE
            p pedido;
            credito movimento_pontos;
            ja_estornado integer;
            devolvido numeric;
            devido integer;
            disponivel integer;
        BEGIN
            IF NEW.tipo <> 'estorno' OR NEW.status <> 'aprovado' OR NEW.origem = 'migracao' THEN
                RETURN NEW;
            END IF;
            -- o crédito do pedido, da plataforma ou da carga (devolução de um pedido migrado, V13)
            SELECT * INTO credito FROM movimento_pontos WHERE id_pedido = NEW.id_pedido AND tipo = 'credito';
            IF credito.id_movimento IS NULL THEN
                RETURN NEW;
            END IF;
            SELECT * INTO p FROM pedido WHERE id_pedido = NEW.id_pedido;

            SELECT coalesce(-sum(pontos), 0) INTO ja_estornado FROM movimento_pontos
            WHERE tipo = 'estorno' AND id_movimento_original = credito.id_movimento;
            SELECT sum(valor) INTO devolvido FROM pagamento
            WHERE id_pedido = p.id_pedido AND tipo = 'estorno' AND status = 'aprovado'
                AND origem IS DISTINCT FROM 'migracao';
            devido := least(credito.pontos, round(credito.pontos * devolvido / p.valor_total)::integer) - ja_estornado;

            -- o dono é o de hoje: o crédito de um cliente sem conta passou para a conta dele
            SELECT saldo INTO disponivel FROM saldo_pontos WHERE marca = credito.marca
                AND id_cliente IS NOT DISTINCT FROM p.id_cliente
                AND id_cliente_sem_conta IS NOT DISTINCT FROM p.id_cliente_sem_conta;
            devido := least(devido, greatest(coalesce(disponivel, 0), 0));
            IF devido > 0 THEN
                INSERT INTO movimento_pontos (id_cliente, id_cliente_sem_conta, marca, tipo, pontos, origem, id_pedido,
                                              id_movimento_original, motivo)
                VALUES (p.id_cliente, p.id_cliente_sem_conta, credito.marca, 'estorno', -devido, 'plataforma',
                        p.id_pedido, credito.id_movimento, 'Dinheiro devolvido ao cliente');
            END IF;
            RETURN NEW;
        END;
        $$;
    """),
]
TRIGGERS = [
    ("trg_credita_pedido_pago", "AFTER INSERT OR UPDATE OF status", "credita_pedido_pago()"),
    ("trg_estorna_pontos_do_pagamento", "AFTER INSERT", "estorna_pontos_do_pagamento()"),
]


def upgrade() -> None:
    """Upgrade schema."""
    for assinatura, corpo in FUNCOES:
        op.execute(corpo)
        op.execute(f"REVOKE ALL ON FUNCTION {assinatura} FROM PUBLIC, anon, authenticated;")
    for nome, quando, funcao in TRIGGERS:
        op.execute(f"CREATE TRIGGER {nome} {quando} ON pagamento FOR EACH ROW EXECUTE FUNCTION {funcao};")


def downgrade() -> None:
    """Downgrade schema."""
    for nome, _, _ in reversed(TRIGGERS):
        op.execute(f"DROP TRIGGER {nome} ON pagamento;")
    for assinatura, _ in reversed(FUNCOES):
        op.execute(f"DROP FUNCTION {assinatura};")
