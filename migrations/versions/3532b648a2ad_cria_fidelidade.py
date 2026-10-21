"""cria fidelidade

Revision ID: 3532b648a2ad
Revises: 57c4e9a323e3
Create Date: 2026-10-20 22:28:33.082936

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3532b648a2ad'
down_revision: Union[str, Sequence[str], None] = '57c4e9a323e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Fidelidade (P5, K3, K4a, K4c, P10, registro de decisões da migração da Vulto): o programa de pontos de
# cada marca, no padrão do estoque (ADR 0005 e 0006). O extrato (movimento_pontos) nunca muda; o saldo,
# a categoria e o que sobra de cada entrada de pontos (lote_pontos) só mudam pelo trigger
# aplica_movimento_pontos e pelos jobs do pg_cron, que rodam com o dono do banco (SECURITY DEFINER): a API
# só lê o saldo, os lotes e o consumo. Regras e extrato são histórico: a API só lê e insere, e o
# bloqueia_alteracao (migration e3f070d83fd6) recusa alterar ou apagar

FUSO = "America/Sao_Paulo"
IMUTAVEIS = ["regra_fidelidade", "movimento_pontos", "consumo_pontos"]
TAREFAS = [
    # 00h10 de Brasília: dá validade aos lotes e vence o que passou da data
    ("vence-pontos", "10 3 * * *", "SELECT vence_pontos()"),
    # 00h05 de Brasília de 1º de janeiro: categoria pelos pontos do ano que fechou
    ("vira-ano-fidelidade", "5 3 1 1 *", "SELECT vira_ano_fidelidade()"),
]

# regras da Vulto: R$ 0,10 até fev/2025 e R$ 0,05 depois (K3); dobro no online desde sempre (K4b); bônus
# Ouro e vencimento só a partir da migração (K4a). A Lorenzi começa com a mesma regra da Vulto (P10)
REGRAS = [
    # marca, valida_desde, valor_ponto, bonus_ouro, validade, validade no Ouro
    ("vulto", "2025-01-01", "0.10", "0", "NULL", "NULL"),
    ("vulto", "2025-03-01", "0.05", "0", "NULL", "NULL"),
    ("vulto", "2026-10-01", "0.05", "0.5", "12", "18"),
    ("lorenzi", "2026-10-01", "0.05", "0.5", "12", "18"),
]

FUNCOES = [
    # regra da marca na data (pelo dia de Brasília)
    ("regra_vigente(text, timestamptz)", f"""
        CREATE FUNCTION regra_vigente(p_marca text, p_quando timestamptz) RETURNS regra_fidelidade
        LANGUAGE sql STABLE SET search_path = public AS $$
            SELECT * FROM regra_fidelidade
            WHERE marca = p_marca AND valida_desde <= (p_quando AT TIME ZONE '{FUSO}')::date
            ORDER BY valida_desde DESC LIMIT 1;
        $$;
    """),
    ("categoria_pelos_pontos(regra_fidelidade, integer)", """
        CREATE FUNCTION categoria_pelos_pontos(r regra_fidelidade, p_ganhos integer) RETURNS text
        LANGUAGE sql IMMUTABLE AS $$
            SELECT CASE WHEN p_ganhos >= r.limite_ouro THEN 'ouro'
                        WHEN p_ganhos >= r.limite_prata THEN 'prata' ELSE 'bronze' END;
        $$;
    """),
    # virada do ano (K4c): a categoria passa a ser a dos pontos ganhos no ano que fechou; ano sem
    # movimento conta como zero
    ("vira_ano_pontos(integer, integer)", f"""
        CREATE FUNCTION vira_ano_pontos(p_id_saldo integer, p_ano integer) RETURNS void
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
        DECLARE
            s saldo_pontos;
            inicio timestamptz := make_date(p_ano, 1, 1)::timestamp AT TIME ZONE '{FUSO}';
            r regra_fidelidade;
        BEGIN
            SELECT * INTO s FROM saldo_pontos WHERE id_saldo = p_id_saldo FOR UPDATE;
            IF s.ano_referencia >= p_ano THEN
                RETURN;
            END IF;
            r := regra_vigente(s.marca, inicio);
            UPDATE saldo_pontos SET
                categoria = CASE WHEN r.id_regra IS NULL THEN 'bronze' ELSE categoria_pelos_pontos(
                    r, CASE WHEN s.ano_referencia = p_ano - 1 THEN s.ganhos_no_ano ELSE 0 END) END,
                categoria_desde = inicio, ano_referencia = p_ano, ganhos_no_ano = 0, atualizado_em = now()
            WHERE id_saldo = p_id_saldo;
        END;
        $$;
    """),
    ("aplica_movimento_pontos()", f"""
        CREATE FUNCTION aplica_movimento_pontos() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
        DECLARE
            r regra_fidelidade;
            s saldo_pontos;
            orig movimento_pontos;
            l record;
            ano integer := extract(year FROM NEW.criado_em AT TIME ZONE '{FUSO}');
            ordem text[] := ARRAY['bronze', 'prata', 'ouro'];
            categoria_nova text;
            ganhos integer;
            falta integer;
            parte integer;
            restante_novo integer;
        BEGIN
            r := regra_vigente(NEW.marca, NEW.criado_em);
            IF r.id_regra IS NULL THEN
                RAISE EXCEPTION 'A marca % não tem regra de fidelidade em %', NEW.marca, NEW.criado_em
                    USING ERRCODE = 'check_violation';
            END IF;

            -- o movimento original é da mesma marca e do mesmo cliente (ou do cliente sem conta que passou
            -- para esta conta); o estorno tem o sinal contrário dele
            IF NEW.id_movimento_original IS NOT NULL THEN
                SELECT * INTO orig FROM movimento_pontos WHERE id_movimento = NEW.id_movimento_original;
                IF orig.marca <> NEW.marca OR NOT (
                    (orig.id_cliente IS NOT DISTINCT FROM NEW.id_cliente
                        AND orig.id_cliente_sem_conta IS NOT DISTINCT FROM NEW.id_cliente_sem_conta)
                    OR EXISTS (SELECT 1 FROM cliente_sem_conta c WHERE c.id_cliente_sem_conta = orig.id_cliente_sem_conta
                               AND c.id_usuario = NEW.id_cliente)
                ) THEN
                    RAISE EXCEPTION 'O movimento original é de outro cliente ou de outra marca'
                        USING ERRCODE = 'check_violation';
                END IF;
                IF NEW.tipo = 'estorno' AND sign(NEW.pontos) = sign(orig.pontos) THEN
                    RAISE EXCEPTION 'O estorno tem o sinal contrário do movimento original'
                        USING ERRCODE = 'check_violation';
                END IF;
            END IF;

            -- garante a linha do saldo e a trava, para dois movimentos simultâneos não lerem o mesmo saldo
            INSERT INTO saldo_pontos (id_cliente, id_cliente_sem_conta, marca, categoria_desde, ano_referencia)
            VALUES (NEW.id_cliente, NEW.id_cliente_sem_conta, NEW.marca, NEW.criado_em, ano)
            ON CONFLICT DO NOTHING;
            SELECT * INTO s FROM saldo_pontos
            WHERE marca = NEW.marca AND id_cliente IS NOT DISTINCT FROM NEW.id_cliente
                AND id_cliente_sem_conta IS NOT DISTINCT FROM NEW.id_cliente_sem_conta
            FOR UPDATE;
            IF s.ano_referencia > ano THEN
                RAISE EXCEPTION 'Movimento de % depois de movimentos de %: a carga entra em ordem de data',
                    ano, s.ano_referencia USING ERRCODE = 'check_violation';
            ELSIF s.ano_referencia < ano THEN
                PERFORM vira_ano_pontos(s.id_saldo, ano);
                SELECT * INTO s FROM saldo_pontos WHERE id_saldo = s.id_saldo;
            END IF;

            -- daqui em diante o saldo nunca fica negativo; o histórico da Vulto pode ter ficado (K5f)
            IF NEW.origem = 'plataforma' AND s.saldo + NEW.pontos < 0 THEN
                RAISE EXCEPTION 'Pontos insuficientes: saldo %, movimento %', s.saldo, NEW.pontos
                    USING ERRCODE = 'check_violation';
            END IF;

            -- pontos ganhos no ano: o crédito e o que corrige um crédito do mesmo ano; a categoria sobe na hora
            ganhos := s.ganhos_no_ano;
            IF NEW.tipo = 'credito' OR (NEW.tipo IN ('estorno', 'ajuste') AND orig.tipo = 'credito'
                    AND extract(year FROM orig.criado_em AT TIME ZONE '{FUSO}') = ano) THEN
                ganhos := ganhos + NEW.pontos;
                categoria_nova := categoria_pelos_pontos(r, ganhos);
                IF array_position(ordem, categoria_nova) > array_position(ordem, s.categoria) THEN
                    s.categoria := categoria_nova;
                    s.categoria_desde := NEW.criado_em;
                END IF;
            END IF;

            IF NEW.pontos > 0 THEN
                -- entrada: primeiro cobre o saldo negativo herdado, e o resto fica disponível
                restante_novo := NEW.pontos - least(NEW.pontos, greatest(-s.saldo, 0));
                IF NEW.tipo = 'estorno' THEN
                    -- devolução de uma saída: volta para os lotes de onde saiu, com a época e o valor deles
                    falta := restante_novo;
                    FOR l IN
                        SELECT co.id_lote, sum(co.pontos) AS liquido
                        FROM consumo_pontos co JOIN movimento_pontos m ON m.id_movimento = co.id_movimento
                        JOIN lote_pontos lo ON lo.id_movimento = co.id_lote
                        WHERE m.id_movimento = orig.id_movimento
                            OR (m.tipo = 'estorno' AND m.id_movimento_original = orig.id_movimento)
                        GROUP BY co.id_lote, lo.ganho_em HAVING sum(co.pontos) > 0
                        ORDER BY lo.ganho_em DESC, co.id_lote DESC
                    LOOP
                        EXIT WHEN falta = 0;
                        parte := least(falta, l.liquido);
                        UPDATE lote_pontos SET restante = restante + parte WHERE id_movimento = l.id_lote;
                        INSERT INTO consumo_pontos (id_movimento, id_lote, pontos) VALUES (NEW.id_movimento, l.id_lote, -parte);
                        falta := falta - parte;
                    END LOOP;
                    IF falta > 0 THEN
                        RAISE EXCEPTION 'O estorno devolve mais pontos do que saíram do movimento original'
                            USING ERRCODE = 'check_violation';
                    END IF;
                ELSE
                    INSERT INTO lote_pontos (id_movimento, id_cliente, id_cliente_sem_conta, marca, ganho_em,
                                             valor_ponto, pontos, restante, vence_em)
                    VALUES (NEW.id_movimento, NEW.id_cliente, NEW.id_cliente_sem_conta, NEW.marca, NEW.criado_em,
                            r.valor_ponto, NEW.pontos, restante_novo,
                            NEW.criado_em + make_interval(months => CASE WHEN s.categoria = 'ouro'
                                THEN r.validade_meses_ouro ELSE r.validade_meses END));
                END IF;
            ELSE
                -- saída: o vencimento, o estorno e o ajuste de uma entrada saem primeiro do lote dela (K3)
                falta := -NEW.pontos;
                IF orig.id_movimento IS NOT NULL AND orig.pontos > 0 THEN
                    SELECT id_movimento, restante INTO l FROM lote_pontos WHERE id_movimento = orig.id_movimento FOR UPDATE;
                    parte := least(falta, coalesce(l.restante, 0));
                    IF parte > 0 THEN
                        UPDATE lote_pontos SET restante = restante - parte WHERE id_movimento = orig.id_movimento;
                        INSERT INTO consumo_pontos (id_movimento, id_lote, pontos) VALUES (NEW.id_movimento, orig.id_movimento, parte);
                        falta := falta - parte;
                    END IF;
                END IF;
                IF NEW.tipo = 'vencimento' THEN
                    IF falta > 0 THEN
                        RAISE EXCEPTION 'O vencimento leva mais pontos do que sobraram no lote'
                            USING ERRCODE = 'check_violation';
                    END IF;
                ELSE
                    -- o resto sai dos lotes mais antigos (FIFO)
                    FOR l IN
                        SELECT id_movimento, restante FROM lote_pontos
                        WHERE marca = NEW.marca AND restante > 0
                            AND id_cliente IS NOT DISTINCT FROM NEW.id_cliente
                            AND id_cliente_sem_conta IS NOT DISTINCT FROM NEW.id_cliente_sem_conta
                        ORDER BY ganho_em, id_movimento
                        FOR UPDATE
                    LOOP
                        EXIT WHEN falta = 0;
                        parte := least(falta, l.restante);
                        UPDATE lote_pontos SET restante = restante - parte WHERE id_movimento = l.id_movimento;
                        INSERT INTO consumo_pontos (id_movimento, id_lote, pontos) VALUES (NEW.id_movimento, l.id_movimento, parte);
                        falta := falta - parte;
                    END LOOP;
                    -- o que faltar deixa o saldo negativo: só no histórico da migração (K5f)
                END IF;
            END IF;

            UPDATE saldo_pontos SET saldo = s.saldo + NEW.pontos, categoria = s.categoria,
                categoria_desde = s.categoria_desde, ganhos_no_ano = ganhos, atualizado_em = now()
            WHERE id_saldo = s.id_saldo;
            RETURN NEW;
        END;
        $$;
    """),
    # job diário (K4a): a entrada sem validade ganha a da regra a partir de quando a marca passou a ter
    # vencimento (o saldo da migração vence em 01/10/2027, 01/04/2028 no Ouro), e o que passou da data vence
    ("vence_pontos()", f"""
        CREATE FUNCTION vence_pontos() RETURNS integer
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
        DECLARE
            l record;
            r regra_fidelidade;
            inicio timestamptz;
            vencidos integer := 0;
        BEGIN
            FOR l IN
                SELECT lo.id_movimento, lo.marca, lo.ganho_em, s.categoria
                FROM lote_pontos lo JOIN saldo_pontos s ON s.marca = lo.marca
                    AND s.id_cliente IS NOT DISTINCT FROM lo.id_cliente
                    AND s.id_cliente_sem_conta IS NOT DISTINCT FROM lo.id_cliente_sem_conta
                WHERE lo.vence_em IS NULL AND lo.restante > 0
            LOOP
                r := regra_vigente(l.marca, now());
                CONTINUE WHEN r.validade_meses IS NULL;
                SELECT min(valida_desde)::timestamp AT TIME ZONE '{FUSO}' INTO inicio FROM regra_fidelidade
                WHERE marca = l.marca AND validade_meses IS NOT NULL
                    AND valida_desde <= (now() AT TIME ZONE '{FUSO}')::date;
                UPDATE lote_pontos SET vence_em = greatest(l.ganho_em, inicio) + make_interval(
                    months => CASE WHEN l.categoria = 'ouro' THEN r.validade_meses_ouro ELSE r.validade_meses END)
                WHERE id_movimento = l.id_movimento;
            END LOOP;

            FOR l IN
                SELECT * FROM lote_pontos WHERE vence_em <= now() AND restante > 0 ORDER BY vence_em, id_movimento
            LOOP
                INSERT INTO movimento_pontos (id_cliente, id_cliente_sem_conta, marca, tipo, pontos, origem,
                                              id_movimento_original, motivo)
                VALUES (l.id_cliente, l.id_cliente_sem_conta, l.marca, 'vencimento', -l.restante, 'plataforma',
                        l.id_movimento, 'Pontos vencidos');
                vencidos := vencidos + 1;
            END LOOP;
            RETURN vencidos;
        END;
        $$;
    """),
    # job de 1º de janeiro: vira o ano de quem não teve movimento (quem teve já virou no primeiro movimento)
    ("vira_ano_fidelidade()", f"""
        CREATE FUNCTION vira_ano_fidelidade() RETURNS integer
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
        DECLARE
            ano integer := extract(year FROM now() AT TIME ZONE '{FUSO}');
            s record;
            viradas integer := 0;
        BEGIN
            FOR s IN SELECT id_saldo FROM saldo_pontos WHERE ano_referencia < ano LOOP
                PERFORM vira_ano_pontos(s.id_saldo, ano);
                viradas := viradas + 1;
            END LOOP;
            RETURN viradas;
        END;
        $$;
    """),
]


def upgrade() -> None:
    """Upgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('regra_fidelidade',
    sa.Column('id_regra', sa.Integer(), nullable=False),
    sa.Column('marca', sa.String(length=10), nullable=False),
    sa.Column('valida_desde', sa.Date(), nullable=False),
    sa.Column('valor_ponto', sa.Numeric(precision=6, scale=4), nullable=False),
    sa.Column('pontos_por_real', sa.Numeric(precision=6, scale=2), nullable=False),
    sa.Column('multiplicador_online', sa.Numeric(precision=4, scale=2), nullable=False),
    sa.Column('bonus_ouro', sa.Numeric(precision=4, scale=2), nullable=False),
    sa.Column('validade_meses', sa.Integer(), nullable=True),
    sa.Column('validade_meses_ouro', sa.Integer(), nullable=True),
    sa.Column('limite_prata', sa.Integer(), nullable=False),
    sa.Column('limite_ouro', sa.Integer(), nullable=False),
    sa.Column('resgate_minimo', sa.Integer(), nullable=False),
    sa.Column('id_criado_por', sa.Uuid(), nullable=True),
    sa.Column('criado_em', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("marca IN ('lorenzi', 'vulto')", name=op.f('ck_regra_fidelidade_marca_valida')),
    sa.CheckConstraint('(validade_meses IS NULL) = (validade_meses_ouro IS NULL) AND (validade_meses IS NULL OR (validade_meses > 0 AND validade_meses_ouro >= validade_meses))', name=op.f('ck_regra_fidelidade_validade_valida')),
    sa.CheckConstraint('0 < limite_prata AND limite_prata < limite_ouro', name=op.f('ck_regra_fidelidade_limites_validos')),
    sa.CheckConstraint('multiplicador_online >= 1 AND bonus_ouro >= 0', name=op.f('ck_regra_fidelidade_bonus_validos')),
    sa.CheckConstraint('resgate_minimo > 0', name=op.f('ck_regra_fidelidade_resgate_minimo_positivo')),
    sa.CheckConstraint('valor_ponto > 0 AND pontos_por_real >= 0', name=op.f('ck_regra_fidelidade_valores_validos')),
    sa.ForeignKeyConstraint(['id_criado_por'], ['usuario.id_usuario'], name=op.f('fk_regra_fidelidade_id_criado_por_usuario'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id_regra', name=op.f('pk_regra_fidelidade')),
    sa.UniqueConstraint('marca', 'valida_desde', name=op.f('uq_regra_fidelidade_marca_valida_desde'))
    )
    op.execute('ALTER TABLE "regra_fidelidade" ENABLE ROW LEVEL SECURITY')
    op.execute('CREATE POLICY api_casalorenzi_acesso ON "regra_fidelidade" FOR ALL TO api_casalorenzi USING (true) WITH CHECK (true)')
    op.execute('GRANT SELECT, INSERT ON "regra_fidelidade" TO api_casalorenzi')
    op.execute('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO api_casalorenzi')
    op.create_table('saldo_pontos',
    sa.Column('id_saldo', sa.Integer(), nullable=False),
    sa.Column('id_cliente', sa.Uuid(), nullable=True),
    sa.Column('id_cliente_sem_conta', sa.Integer(), nullable=True),
    sa.Column('marca', sa.String(length=10), nullable=False),
    sa.Column('saldo', sa.Integer(), server_default='0', nullable=False),
    sa.Column('categoria', sa.String(length=10), server_default='bronze', nullable=False),
    sa.Column('categoria_desde', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ano_referencia', sa.Integer(), nullable=False),
    sa.Column('ganhos_no_ano', sa.Integer(), server_default='0', nullable=False),
    sa.Column('atualizado_em', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("categoria IN ('bronze', 'prata', 'ouro')", name=op.f('ck_saldo_pontos_categoria_valida')),
    sa.CheckConstraint("marca IN ('lorenzi', 'vulto')", name=op.f('ck_saldo_pontos_marca_valida')),
    sa.CheckConstraint('num_nonnulls(id_cliente, id_cliente_sem_conta) = 1', name=op.f('ck_saldo_pontos_um_dono')),
    sa.ForeignKeyConstraint(['id_cliente'], ['usuario.id_usuario'], name=op.f('fk_saldo_pontos_id_cliente_usuario'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['id_cliente_sem_conta'], ['cliente_sem_conta.id_cliente_sem_conta'], name=op.f('fk_saldo_pontos_id_cliente_sem_conta_cliente_sem_conta'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id_saldo', name=op.f('pk_saldo_pontos'))
    )
    op.execute('ALTER TABLE "saldo_pontos" ENABLE ROW LEVEL SECURITY')
    op.execute('CREATE POLICY api_casalorenzi_acesso ON "saldo_pontos" FOR ALL TO api_casalorenzi USING (true) WITH CHECK (true)')
    op.execute('GRANT SELECT ON "saldo_pontos" TO api_casalorenzi')
    op.execute('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO api_casalorenzi')
    op.create_index('uq_saldo_pontos_id_cliente_marca', 'saldo_pontos', ['id_cliente', 'marca'], unique=True, postgresql_where=sa.text('id_cliente IS NOT NULL'))
    op.create_index('uq_saldo_pontos_id_cliente_sem_conta_marca', 'saldo_pontos', ['id_cliente_sem_conta', 'marca'], unique=True, postgresql_where=sa.text('id_cliente_sem_conta IS NOT NULL'))
    op.create_table('movimento_pontos',
    sa.Column('id_movimento', sa.Integer(), nullable=False),
    sa.Column('id_cliente', sa.Uuid(), nullable=True),
    sa.Column('id_cliente_sem_conta', sa.Integer(), nullable=True),
    sa.Column('marca', sa.String(length=10), nullable=False),
    sa.Column('tipo', sa.String(length=20), nullable=False),
    sa.Column('pontos', sa.Integer(), nullable=False),
    sa.Column('origem', sa.String(length=10), nullable=False),
    sa.Column('id_pedido', sa.Integer(), nullable=True),
    sa.Column('id_movimento_original', sa.Integer(), nullable=True),
    sa.Column('motivo', sa.Text(), nullable=True),
    sa.Column('id_registrado_por', sa.Uuid(), nullable=True),
    sa.Column('criado_em', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("CASE WHEN tipo IN ('estorno', 'vencimento') THEN id_movimento_original IS NOT NULL WHEN tipo = 'ajuste' THEN true ELSE id_movimento_original IS NULL END", name=op.f('ck_movimento_pontos_original_do_tipo')),
    sa.CheckConstraint("CASE tipo WHEN 'saldo_inicial' THEN pontos > 0 WHEN 'credito' THEN pontos > 0 WHEN 'resgate' THEN pontos < 0 WHEN 'vencimento' THEN pontos < 0 ELSE pontos <> 0 END", name=op.f('ck_movimento_pontos_sinal_do_tipo')),
    sa.CheckConstraint("marca IN ('lorenzi', 'vulto')", name=op.f('ck_movimento_pontos_marca_valida')),
    sa.CheckConstraint("origem <> 'plataforma' OR tipo NOT IN ('credito', 'resgate') OR id_pedido IS NOT NULL", name=op.f('ck_movimento_pontos_plataforma_exige_pedido')),
    sa.CheckConstraint("origem IN ('migracao', 'plataforma')", name=op.f('ck_movimento_pontos_origem_valida')),
    sa.CheckConstraint("tipo <> 'ajuste' OR motivo IS NOT NULL", name=op.f('ck_movimento_pontos_ajuste_exige_motivo')),
    sa.CheckConstraint("tipo IN ('saldo_inicial', 'credito', 'resgate', 'estorno', 'ajuste', 'vencimento')", name=op.f('ck_movimento_pontos_tipo_valido')),
    sa.CheckConstraint('num_nonnulls(id_cliente, id_cliente_sem_conta) = 1', name=op.f('ck_movimento_pontos_um_dono')),
    sa.ForeignKeyConstraint(['id_cliente'], ['usuario.id_usuario'], name=op.f('fk_movimento_pontos_id_cliente_usuario'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['id_cliente_sem_conta'], ['cliente_sem_conta.id_cliente_sem_conta'], name=op.f('fk_movimento_pontos_id_cliente_sem_conta_cliente_sem_conta'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['id_movimento_original'], ['movimento_pontos.id_movimento'], name=op.f('fk_movimento_pontos_id_movimento_original_movimento_pontos'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['id_pedido'], ['pedido.id_pedido'], name=op.f('fk_movimento_pontos_id_pedido_pedido'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['id_registrado_por'], ['usuario.id_usuario'], name=op.f('fk_movimento_pontos_id_registrado_por_usuario'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id_movimento', name=op.f('pk_movimento_pontos'))
    )
    op.execute('ALTER TABLE "movimento_pontos" ENABLE ROW LEVEL SECURITY')
    op.execute('CREATE POLICY api_casalorenzi_acesso ON "movimento_pontos" FOR ALL TO api_casalorenzi USING (true) WITH CHECK (true)')
    op.execute('GRANT SELECT, INSERT ON "movimento_pontos" TO api_casalorenzi')
    op.execute('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO api_casalorenzi')
    op.create_index('ix_movimento_pontos_id_cliente_marca', 'movimento_pontos', ['id_cliente', 'marca', 'criado_em'], unique=False)
    op.create_index('ix_movimento_pontos_id_cliente_sem_conta_marca', 'movimento_pontos', ['id_cliente_sem_conta', 'marca', 'criado_em'], unique=False)
    op.create_index('ix_movimento_pontos_id_pedido', 'movimento_pontos', ['id_pedido'], unique=False)
    op.create_table('lote_pontos',
    sa.Column('id_movimento', sa.Integer(), nullable=False),
    sa.Column('id_cliente', sa.Uuid(), nullable=True),
    sa.Column('id_cliente_sem_conta', sa.Integer(), nullable=True),
    sa.Column('marca', sa.String(length=10), nullable=False),
    sa.Column('ganho_em', sa.DateTime(timezone=True), nullable=False),
    sa.Column('valor_ponto', sa.Numeric(precision=6, scale=4), nullable=False),
    sa.Column('pontos', sa.Integer(), nullable=False),
    sa.Column('restante', sa.Integer(), nullable=False),
    sa.Column('vence_em', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('0 <= restante AND restante <= pontos', name=op.f('ck_lote_pontos_restante_valido')),
    sa.CheckConstraint('num_nonnulls(id_cliente, id_cliente_sem_conta) = 1', name=op.f('ck_lote_pontos_um_dono')),
    sa.ForeignKeyConstraint(['id_cliente'], ['usuario.id_usuario'], name=op.f('fk_lote_pontos_id_cliente_usuario'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['id_cliente_sem_conta'], ['cliente_sem_conta.id_cliente_sem_conta'], name=op.f('fk_lote_pontos_id_cliente_sem_conta_cliente_sem_conta'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['id_movimento'], ['movimento_pontos.id_movimento'], name=op.f('fk_lote_pontos_id_movimento_movimento_pontos'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id_movimento', name=op.f('pk_lote_pontos'))
    )
    op.execute('ALTER TABLE "lote_pontos" ENABLE ROW LEVEL SECURITY')
    op.execute('CREATE POLICY api_casalorenzi_acesso ON "lote_pontos" FOR ALL TO api_casalorenzi USING (true) WITH CHECK (true)')
    op.execute('GRANT SELECT ON "lote_pontos" TO api_casalorenzi')
    op.execute('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO api_casalorenzi')
    op.create_index('ix_lote_pontos_id_cliente_marca', 'lote_pontos', ['id_cliente', 'marca', 'ganho_em'], unique=False, postgresql_where=sa.text('restante > 0'))
    op.create_index('ix_lote_pontos_id_cliente_sem_conta_marca', 'lote_pontos', ['id_cliente_sem_conta', 'marca', 'ganho_em'], unique=False, postgresql_where=sa.text('restante > 0'))
    op.create_index('ix_lote_pontos_vence_em', 'lote_pontos', ['vence_em'], unique=False, postgresql_where=sa.text('restante > 0'))
    op.create_table('consumo_pontos',
    sa.Column('id_consumo', sa.Integer(), nullable=False),
    sa.Column('id_movimento', sa.Integer(), nullable=False),
    sa.Column('id_lote', sa.Integer(), nullable=False),
    sa.Column('pontos', sa.Integer(), nullable=False),
    sa.CheckConstraint('pontos <> 0', name=op.f('ck_consumo_pontos_pontos_diferente_de_zero')),
    sa.ForeignKeyConstraint(['id_lote'], ['lote_pontos.id_movimento'], name=op.f('fk_consumo_pontos_id_lote_lote_pontos'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['id_movimento'], ['movimento_pontos.id_movimento'], name=op.f('fk_consumo_pontos_id_movimento_movimento_pontos'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id_consumo', name=op.f('pk_consumo_pontos'))
    )
    op.execute('ALTER TABLE "consumo_pontos" ENABLE ROW LEVEL SECURITY')
    op.execute('CREATE POLICY api_casalorenzi_acesso ON "consumo_pontos" FOR ALL TO api_casalorenzi USING (true) WITH CHECK (true)')
    op.execute('GRANT SELECT ON "consumo_pontos" TO api_casalorenzi')
    op.execute('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO api_casalorenzi')
    op.create_index('ix_consumo_pontos_id_movimento', 'consumo_pontos', ['id_movimento'], unique=False)
    # ### end Alembic commands ###

    for _, corpo in FUNCOES:
        op.execute(corpo)
    for assinatura, _ in FUNCOES:
        op.execute(f"REVOKE ALL ON FUNCTION {assinatura} FROM PUBLIC, anon, authenticated;")
    op.execute("""
        CREATE TRIGGER trg_aplica_movimento_pontos
        AFTER INSERT ON movimento_pontos
        FOR EACH ROW EXECUTE FUNCTION aplica_movimento_pontos();
    """)
    for tabela in IMUTAVEIS:
        op.execute(f"""
            CREATE TRIGGER trg_bloqueia_alteracao_{tabela}
            BEFORE UPDATE OR DELETE ON {tabela}
            FOR EACH ROW EXECUTE FUNCTION bloqueia_alteracao();
        """)
    for marca, desde, valor, bonus, validade, validade_ouro in REGRAS:
        op.execute(f"""
            INSERT INTO regra_fidelidade (marca, valida_desde, valor_ponto, pontos_por_real, multiplicador_online,
                bonus_ouro, validade_meses, validade_meses_ouro, limite_prata, limite_ouro, resgate_minimo)
            VALUES ('{marca}', '{desde}', {valor}, 1, 2, {bonus}, {validade}, {validade_ouro}, 2000, 5000, 100);
        """)
    for nome, quando, comando in TAREFAS:
        op.execute(f"SELECT cron.schedule('{nome}', '{quando}', '{comando}');")


def downgrade() -> None:
    """Downgrade schema."""
    # só desfaz antes da carga: apaga o programa de pontos inteiro
    for nome, _, _ in reversed(TAREFAS):
        op.execute(f"SELECT cron.unschedule('{nome}');")
    for tabela in reversed(IMUTAVEIS):
        op.execute(f"DROP TRIGGER trg_bloqueia_alteracao_{tabela} ON {tabela};")
    op.execute("DROP TRIGGER trg_aplica_movimento_pontos ON movimento_pontos;")
    for assinatura, _ in reversed(FUNCOES):
        op.execute(f"DROP FUNCTION {assinatura};")
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index('ix_consumo_pontos_id_movimento', table_name='consumo_pontos')
    op.drop_table('consumo_pontos')
    op.drop_index('ix_lote_pontos_vence_em', table_name='lote_pontos', postgresql_where=sa.text('restante > 0'))
    op.drop_index('ix_lote_pontos_id_cliente_sem_conta_marca', table_name='lote_pontos', postgresql_where=sa.text('restante > 0'))
    op.drop_index('ix_lote_pontos_id_cliente_marca', table_name='lote_pontos', postgresql_where=sa.text('restante > 0'))
    op.drop_table('lote_pontos')
    op.drop_index('ix_movimento_pontos_id_pedido', table_name='movimento_pontos')
    op.drop_index('ix_movimento_pontos_id_cliente_sem_conta_marca', table_name='movimento_pontos')
    op.drop_index('ix_movimento_pontos_id_cliente_marca', table_name='movimento_pontos')
    op.drop_table('movimento_pontos')
    op.drop_index('uq_saldo_pontos_id_cliente_sem_conta_marca', table_name='saldo_pontos', postgresql_where=sa.text('id_cliente_sem_conta IS NOT NULL'))
    op.drop_index('uq_saldo_pontos_id_cliente_marca', table_name='saldo_pontos', postgresql_where=sa.text('id_cliente IS NOT NULL'))
    op.drop_table('saldo_pontos')
    op.drop_table('regra_fidelidade')
    # ### end Alembic commands ###
