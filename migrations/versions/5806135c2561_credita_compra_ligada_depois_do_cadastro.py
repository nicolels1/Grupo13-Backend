"""credita compra ligada depois do cadastro

Revision ID: 5806135c2561
Revises: 62556fe492f4
Create Date: 2026-10-21 00:10:00.000000

"""
import importlib.util
from pathlib import Path
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5806135c2561'
down_revision: Union[str, Sequence[str], None] = '62556fe492f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Compra no caixa antes do cadastro (P10): a venda só com o CPF na nota passa para a conta quando a pessoa se
# cadastra (ADR 0014) e, se os pontos daquela compra ainda não teriam vencido, ela os ganha. O ponto vale,
# vence e entra no FIFO pela data da compra (ganho_em), não pela do cadastro. O trigger aplica_movimento_pontos
# (migration 3532b648a2ad) passa a usar essa data

FUSO = "America/Sao_Paulo"


def _aplica_movimento_original() -> str:
    arquivo = Path(__file__).with_name("3532b648a2ad_cria_fidelidade.py")
    spec = importlib.util.spec_from_file_location("cria_fidelidade", arquivo)
    migracao = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migracao)
    corpo = dict(migracao.FUNCOES)["aplica_movimento_pontos()"]
    return corpo.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION")


# o mesmo trigger, com a data em que o ponto foi ganho no lugar da data do lançamento
TROCAS = [
    (f"ano integer := extract(year FROM NEW.criado_em AT TIME ZONE '{FUSO}');",
     f"ano integer := extract(year FROM NEW.criado_em AT TIME ZONE '{FUSO}');\n"
     "            ganho timestamptz := coalesce(NEW.ganho_em, NEW.criado_em);"),
    ("r := regra_vigente(NEW.marca, NEW.criado_em);", "r := regra_vigente(NEW.marca, ganho);"),
    ("IF NEW.tipo = 'credito' OR (NEW.tipo IN ('estorno', 'ajuste')",
     f"IF (NEW.tipo = 'credito' AND extract(year FROM ganho AT TIME ZONE '{FUSO}') = ano)\n"
     "                OR (NEW.tipo IN ('estorno', 'ajuste')"),
    ("extract(year FROM orig.criado_em AT TIME ZONE", "extract(year FROM coalesce(orig.ganho_em, orig.criado_em) AT TIME ZONE"),
    ("VALUES (NEW.id_movimento, NEW.id_cliente, NEW.id_cliente_sem_conta, NEW.marca, NEW.criado_em,",
     "VALUES (NEW.id_movimento, NEW.id_cliente, NEW.id_cliente_sem_conta, NEW.marca, ganho,"),
    ("NEW.criado_em + make_interval(months =>", "ganho + make_interval(months =>"),
]


def _aplica_movimento_com_ganho() -> str:
    corpo = _aplica_movimento_original()
    for antes, depois in TROCAS:
        assert corpo.count(antes) == 1, antes
        corpo = corpo.replace(antes, depois)
    return corpo


# a compra passa para a conta (cadastro, código da notinha ou correção de CPF): ganha os pontos pela regra da
# data da compra, sem o que já foi devolvido, se ainda não teriam vencido. Não vale para a compra que já era de
# alguém (a de um cliente sem conta passa com os pontos dele) nem para o pedido migrado
CREDITA_COMPRA_LIGADA = f"""
    CREATE FUNCTION credita_compra_ligada() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
    DECLARE
        marca_pedido text;
        r regra_fidelidade;
        devolvido numeric;
        pontos_ganhos integer;
    BEGIN
        IF OLD.id_cliente IS NOT NULL OR OLD.id_cliente_sem_conta IS NOT NULL OR NEW.id_cliente IS NULL
            OR NEW.status = 'cancelado' OR NEW.pago_em IS NULL
            OR EXISTS (SELECT 1 FROM movimento_pontos WHERE id_pedido = NEW.id_pedido AND tipo = 'credito')
            -- o pedido migrado da Vulto já traz os pontos dele pela carga
            OR EXISTS (SELECT 1 FROM pagamento WHERE id_pedido = NEW.id_pedido AND origem = 'migracao') THEN
            RETURN NEW;
        END IF;
        SELECT u.marca INTO marca_pedido FROM unidade u WHERE u.id_unidade = NEW.id_unidade;
        r := regra_vigente(marca_pedido, NEW.pago_em);
        IF r.id_regra IS NULL
            OR (r.validade_meses IS NOT NULL AND NEW.pago_em + make_interval(months => r.validade_meses) <= now()) THEN
            RETURN NEW;  -- a marca não tinha programa na data, ou os pontos já teriam vencido
        END IF;
        SELECT coalesce(sum(valor), 0) INTO devolvido FROM pagamento
        WHERE id_pedido = NEW.id_pedido AND tipo = 'estorno' AND status = 'aprovado';
        pontos_ganhos := floor((NEW.valor_total - NEW.valor_frete) * (1 - devolvido / NEW.valor_total)
            * r.pontos_por_real * CASE WHEN NEW.canal = 'online' THEN r.multiplicador_online ELSE 1 END);
        IF pontos_ganhos > 0 THEN
            INSERT INTO movimento_pontos (id_cliente, marca, tipo, pontos, origem, id_pedido, ganho_em, motivo)
            VALUES (NEW.id_cliente, marca_pedido, 'credito', pontos_ganhos, 'plataforma', NEW.id_pedido, NEW.pago_em,
                    'Compra de ' || to_char(NEW.pago_em AT TIME ZONE '{FUSO}', 'DD/MM/YYYY') || ', antes do cadastro');
        END IF;
        RETURN NEW;
    END;
    $$;
"""


def upgrade() -> None:
    """Upgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('movimento_pontos', sa.Column('ganho_em', sa.DateTime(timezone=True), nullable=True))
    # ### end Alembic commands ###
    op.create_check_constraint(
        op.f('ck_movimento_pontos_ganho_em_valido'), 'movimento_pontos',
        "ganho_em IS NULL OR (tipo = 'credito' AND ganho_em <= criado_em)",
    )
    op.execute(_aplica_movimento_com_ganho())
    op.execute(CREDITA_COMPRA_LIGADA)
    op.execute("REVOKE ALL ON FUNCTION credita_compra_ligada() FROM PUBLIC, anon, authenticated;")
    op.execute("""
        CREATE TRIGGER trg_credita_compra_ligada
        AFTER UPDATE OF id_cliente ON pedido
        FOR EACH ROW EXECUTE FUNCTION credita_compra_ligada();
    """)


def downgrade() -> None:
    """Downgrade schema."""
    # só desfaz enquanto nenhum crédito com data de compra estiver no banco
    op.execute("DROP TRIGGER trg_credita_compra_ligada ON pedido;")
    op.execute("DROP FUNCTION credita_compra_ligada();")
    op.execute(_aplica_movimento_original())
    op.drop_constraint(op.f('ck_movimento_pontos_ganho_em_valido'), 'movimento_pontos', type_='check')
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('movimento_pontos', 'ganho_em')
    # ### end Alembic commands ###
