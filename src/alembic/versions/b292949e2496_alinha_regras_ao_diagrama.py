"""alinha regras ao diagrama

Revision ID: b292949e2496
Revises: e3f070d83fd6
Create Date: 2026-10-05

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b292949e2496'
down_revision: Union[str, Sequence[str], None] = 'e3f070d83fd6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# o --autogenerate não compara CHECKs: as trocas são feitas à mão
# (tabela, nome, condição nova, condição antiga)
CHECKS_TROCADOS = [
    ("pedido", "ck_pedido_status_valido",
     "status IN ('aguardando_pagamento', 'pago', 'enviado', 'pronto_para_retirada', 'entregue', 'cancelado')",
     "status IN ('aguardando_pagamento', 'pago', 'enviado', 'pronto_retirada', 'entregue', 'cancelado')"),
    ("pagamento", "ck_pagamento_metodo_valido",
     "metodo IN ('pix', 'cartao_credito', 'cartao_debito', 'dinheiro')",
     "metodo IN ('pix', 'credito', 'debito', 'dinheiro')"),
    ("pagamento", "ck_pagamento_status_valido",
     "status IN ('pendente', 'aprovado', 'recusado')",
     "status IN ('pendente', 'aprovado', 'recusado', 'expirado')"),
    ("chamado", "ck_chamado_categoria_valida",
     "categoria IN ('entrega', 'troca_devolucao', 'estorno', 'duvida', 'outros')",
     "categoria IN ('duvida', 'problema_pedido', 'troca', 'devolucao', 'outro')"),
    ("transferencia", "ck_transferencia_cancelada_exige_motivo",
     "status <> 'cancelada' OR (motivo_cancelamento IS NOT NULL AND id_cancelado_por IS NOT NULL)",
     "status <> 'cancelada' OR motivo_cancelamento IS NOT NULL"),
    ("avaliacao", "ck_avaliacao_oculta_exige_motivo",
     "status <> 'oculta' OR (motivo_ocultacao IS NOT NULL AND id_ocultada_por IS NOT NULL AND ocultada_em IS NOT NULL)",
     "status <> 'oculta' OR (motivo_ocultacao IS NOT NULL AND id_ocultada_por IS NOT NULL)"),
]

CHECKS_NOVOS = [
    ("estoque", "ck_estoque_reservada_ate_quantidade", "quantidade_reservada <= quantidade"),
    ("estoque", "ck_estoque_reserva_so_online", "canal = 'online' OR quantidade_reservada = 0"),
    ("item_transferencia", "ck_item_transferencia_recebida_ate_enviada",
     "quantidade_recebida IS NULL OR quantidade_recebida <= quantidade_enviada"),
    ("pagamento", "ck_pagamento_chamado_so_em_estorno", "tipo = 'estorno' OR id_chamado IS NULL"),
]

# o diagrama não restringe o campo alterado do histórico
CHECKS_REMOVIDOS = [
    ("historico_chamado", "ck_historico_chamado_campo_valido",
     "campo_alterado IN ('status', 'responsavel', 'prioridade')"),
]


# funções do ADR 0009 com a regra do diagrama: o Admin recusa só exceção de retirada
EXCECAO_ADMIN_NOVA = """
    CREATE OR REPLACE FUNCTION bloqueia_excecao_admin() RETURNS trigger
    LANGUAGE plpgsql SET search_path = public AS $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM usuario WHERE id_usuario = NEW.id_usuario AND tipo_conta = 'interna') THEN
            RAISE EXCEPTION 'Exceção de permissão só para conta interna'
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.efeito = 'retirar' AND EXISTS (
            SELECT 1 FROM usuario u
            JOIN modelo_acesso m ON m.id_modelo = u.id_modelo_acesso
            WHERE u.id_usuario = NEW.id_usuario AND m.eh_admin
        ) THEN
            RAISE EXCEPTION 'Admin não aceita exceção de retirada'
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END;
    $$;
"""
EXCECAO_ADMIN_ANTIGA = """
    CREATE OR REPLACE FUNCTION bloqueia_excecao_admin() RETURNS trigger
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
"""


def limpa_usuario_admin(filtro: str) -> str:
    return f"""
    CREATE OR REPLACE FUNCTION limpa_excecoes_usuario_admin() RETURNS trigger
    LANGUAGE plpgsql SET search_path = public AS $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM modelo_acesso
            WHERE id_modelo = NEW.id_modelo_acesso AND eh_admin
        ) THEN
            DELETE FROM usuario_permissao_excecao WHERE id_usuario = NEW.id_usuario{filtro};
        END IF;
        RETURN NEW;
    END;
    $$;
"""


def limpa_modelo_admin(filtro: str) -> str:
    return f"""
    CREATE OR REPLACE FUNCTION limpa_excecoes_modelo_admin() RETURNS trigger
    LANGUAGE plpgsql SET search_path = public AS $$
    BEGIN
        IF NEW.eh_admin AND NOT OLD.eh_admin THEN
            DELETE FROM usuario_permissao_excecao e
            USING usuario u
            WHERE e.id_usuario = u.id_usuario AND u.id_modelo_acesso = NEW.id_modelo{filtro};
        END IF;
        RETURN NEW;
    END;
    $$;
"""


# regras do diagrama que envolvem mais de uma tabela: (nome, tabela, quando, corpo)
TRIGGERS_NOVOS = [
    # sempre há um Admin ativo
    ("garante_admin_ativo", "usuario", "AFTER UPDATE OF status_conta, id_modelo_acesso OR DELETE", """
        IF OLD.status_conta = 'ativa'
           AND EXISTS (SELECT 1 FROM modelo_acesso WHERE id_modelo = OLD.id_modelo_acesso AND eh_admin)
           AND NOT EXISTS (
               SELECT 1 FROM usuario u JOIN modelo_acesso m ON m.id_modelo = u.id_modelo_acesso
               WHERE m.eh_admin AND u.status_conta = 'ativa'
           ) THEN
            RAISE EXCEPTION 'Sempre precisa existir uma conta ativa no Admin'
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NULL;
    """),
    # modelo com pessoas ligadas não é desativado; o Admin continua admin
    ("protege_modelo_acesso", "modelo_acesso", "BEFORE UPDATE OF ativo, eh_admin", """
        IF OLD.ativo AND NOT NEW.ativo
           AND EXISTS (SELECT 1 FROM usuario WHERE id_modelo_acesso = OLD.id_modelo) THEN
            RAISE EXCEPTION 'Modelo com pessoas ligadas não pode ser desativado'
                USING ERRCODE = 'check_violation';
        END IF;
        IF OLD.eh_admin AND NOT NEW.eh_admin THEN
            RAISE EXCEPTION 'O modelo Admin não pode deixar de ser admin'
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    """),
    # o CD não faz retirada nem venda física
    ("cd_sem_retirada_nem_venda_fisica", "pedido", "BEFORE INSERT OR UPDATE OF id_unidade, canal, modalidade", """
        IF (NEW.canal = 'loja_fisica' OR NEW.modalidade = 'retirada')
           AND EXISTS (SELECT 1 FROM unidade WHERE id_unidade = NEW.id_unidade AND tipo = 'cd') THEN
            RAISE EXCEPTION 'CD não faz retirada nem venda física'
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    """),
    # item que sai do CD sai do canal online
    ("cd_envia_do_online", "item_transferencia", "BEFORE INSERT OR UPDATE OF id_transferencia, canal_saida", """
        IF NEW.canal_saida <> 'online' AND EXISTS (
            SELECT 1 FROM transferencia t JOIN unidade un ON un.id_unidade = t.id_unidade_origem
            WHERE t.id_transferencia = NEW.id_transferencia AND un.tipo = 'cd'
        ) THEN
            RAISE EXCEPTION 'Item que sai do CD sai do canal online'
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    """),
    # estorno volta pelo mesmo método do pagamento original
    ("estorno_mesmo_metodo", "pagamento", "BEFORE INSERT OR UPDATE OF id_pagamento_original, metodo", """
        IF NEW.id_pagamento_original IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM pagamento WHERE id_pagamento = NEW.id_pagamento_original AND metodo = NEW.metodo
        ) THEN
            RAISE EXCEPTION 'Estorno usa o mesmo método do pagamento original'
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    """),
    # o item do chamado pertence ao pedido do chamado
    ("item_do_pedido_do_chamado", "chamado", "BEFORE INSERT OR UPDATE OF id_item_pedido, id_pedido", """
        IF NEW.id_item_pedido IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM item_pedido WHERE id_item = NEW.id_item_pedido AND id_pedido = NEW.id_pedido
        ) THEN
            RAISE EXCEPTION 'O item do chamado precisa pertencer ao pedido do chamado'
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    """),
]


def upgrade() -> None:
    """Upgrade schema."""
    for tabela, nome, nova, _ in CHECKS_TROCADOS:
        op.drop_constraint(op.f(nome), tabela, type_="check")
        op.create_check_constraint(op.f(nome), tabela, nova)
    for tabela, nome, condicao in CHECKS_NOVOS:
        op.create_check_constraint(op.f(nome), tabela, condicao)
    for tabela, nome, _ in CHECKS_REMOVIDOS:
        op.drop_constraint(op.f(nome), tabela, type_="check")

    op.execute(EXCECAO_ADMIN_NOVA)
    op.execute(limpa_usuario_admin(" AND efeito = 'retirar'"))
    op.execute(limpa_modelo_admin(" AND e.efeito = 'retirar'"))

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

    op.execute(limpa_modelo_admin(""))
    op.execute(limpa_usuario_admin(""))
    op.execute(EXCECAO_ADMIN_ANTIGA)

    for tabela, nome, condicao in CHECKS_REMOVIDOS:
        op.create_check_constraint(op.f(nome), tabela, condicao)
    for tabela, nome, _ in reversed(CHECKS_NOVOS):
        op.drop_constraint(op.f(nome), tabela, type_="check")
    for tabela, nome, _, antiga in reversed(CHECKS_TROCADOS):
        op.drop_constraint(op.f(nome), tabela, type_="check")
        op.create_check_constraint(op.f(nome), tabela, antiga)
