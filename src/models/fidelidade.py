import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Numeric, String, Text, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base
from src.models.estoque import MARCAS

# dono dos pontos: cliente com conta ou cliente sem conta da migração da Vulto (P2), nunca os dois
UM_DONO = "num_nonnulls(id_cliente, id_cliente_sem_conta) = 1"
CATEGORIAS = "('bronze', 'prata', 'ouro')"


# regras do programa de fidelidade de cada marca, com a data em que passam a valer (P5, K3, K4a, P10).
# Regra nova é uma linha nova, só para uma data futura (trigger regra_so_no_futuro), e vale da data dela em
# diante: o ponto ganho guarda o valor da época. Para a mesma data vale a criada por último, assim uma regra
# que ainda não começou se corrige com outra. Nunca alterada (trigger bloqueia_alteracao)
class RegraFidelidade(Base):
    __tablename__ = "regra_fidelidade"

    id_regra: Mapped[int] = mapped_column(primary_key=True)
    marca: Mapped[str] = mapped_column(String(10))
    valida_desde: Mapped[date] = mapped_column(Date)
    valor_ponto: Mapped[Decimal] = mapped_column(Numeric(6, 4))  # em R$
    pontos_por_real: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    multiplicador_online: Mapped[Decimal] = mapped_column(Numeric(4, 2))  # site e app
    bonus_ouro: Mapped[Decimal] = mapped_column(Numeric(4, 2))  # 0,5 = +50% no crédito do Ouro
    # vazio: o ponto não vence (como na Vulto antes da migração, K4a)
    validade_meses: Mapped[int | None] = mapped_column()
    validade_meses_ouro: Mapped[int | None] = mapped_column()
    limite_prata: Mapped[int] = mapped_column()  # pontos ganhos no ano para subir de categoria
    limite_ouro: Mapped[int] = mapped_column()
    resgate_minimo: Mapped[int] = mapped_column()
    id_criado_por: Mapped[uuid.UUID | None] = mapped_column(  # vazio nas regras da migração
        Uuid, ForeignKey("usuario.id_usuario", ondelete="RESTRICT")
    )
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(f"marca IN {MARCAS}", name="marca_valida"),
        CheckConstraint("valor_ponto > 0 AND pontos_por_real >= 0", name="valores_validos"),
        CheckConstraint("multiplicador_online >= 1 AND bonus_ouro >= 0", name="bonus_validos"),
        CheckConstraint(
            "(validade_meses IS NULL) = (validade_meses_ouro IS NULL) "
            "AND (validade_meses IS NULL OR (validade_meses > 0 AND validade_meses_ouro >= validade_meses))",
            name="validade_valida",
        ),
        CheckConstraint("0 < limite_prata AND limite_prata < limite_ouro", name="limites_validos"),
        CheckConstraint("resgate_minimo > 0", name="resgate_minimo_positivo"),
        # regra vigente de cada marca (regra_vigente)
        Index("ix_regra_fidelidade_marca_valida_desde", "marca", "valida_desde"),
    )


# extrato de pontos: o saldo só muda por movimento (trigger aplica_movimento_pontos, como o estoque,
# ADR 0005). Nunca alterado (trigger bloqueia_alteracao); quando o cliente sem conta cria a conta, o
# saldo e os lotes passam para ela e os movimentos continuam com o dono original
class MovimentoPontos(Base):
    __tablename__ = "movimento_pontos"

    id_movimento: Mapped[int] = mapped_column(primary_key=True)
    id_cliente: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("usuario.id_usuario", ondelete="RESTRICT"))
    id_cliente_sem_conta: Mapped[int | None] = mapped_column(
        ForeignKey("cliente_sem_conta.id_cliente_sem_conta", ondelete="RESTRICT")
    )
    marca: Mapped[str] = mapped_column(String(10))  # cada marca tem o seu saldo (P10)
    tipo: Mapped[str] = mapped_column(String(20))
    pontos: Mapped[int] = mapped_column()  # positivo entra, negativo sai
    # migracao: histórico da Vulto, que pode ter deixado o saldo negativo (K5f); plataforma: nunca negativo
    origem: Mapped[str] = mapped_column(String(10))
    id_pedido: Mapped[int | None] = mapped_column(ForeignKey("pedido.id_pedido", ondelete="RESTRICT"))
    # estorno: o movimento que ele desfaz; vencimento: o crédito que venceu; ajuste: o crédito corrigido
    id_movimento_original: Mapped[int | None] = mapped_column(
        ForeignKey("movimento_pontos.id_movimento", ondelete="RESTRICT")
    )
    motivo: Mapped[str | None] = mapped_column(Text)
    id_registrado_por: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("usuario.id_usuario", ondelete="RESTRICT")
    )
    # a carga grava a data original; os movimentos entram em ordem de data (o FIFO segue a ordem)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(UM_DONO, name="um_dono"),
        CheckConstraint(f"marca IN {MARCAS}", name="marca_valida"),
        CheckConstraint(
            "tipo IN ('saldo_inicial', 'credito', 'resgate', 'estorno', 'ajuste', 'vencimento')", name="tipo_valido"
        ),
        CheckConstraint("origem IN ('migracao', 'plataforma')", name="origem_valida"),
        CheckConstraint(
            "CASE tipo WHEN 'saldo_inicial' THEN pontos > 0 WHEN 'credito' THEN pontos > 0 "
            "WHEN 'resgate' THEN pontos < 0 WHEN 'vencimento' THEN pontos < 0 ELSE pontos <> 0 END",
            name="sinal_do_tipo",
        ),
        # daqui em diante, todo ponto ganho ou usado aponta para o pedido; na migração, a venda achada
        # liga ao pedido e o resto se explica pela origem da migração (P4)
        CheckConstraint(
            "origem <> 'plataforma' OR tipo NOT IN ('credito', 'resgate') OR id_pedido IS NOT NULL",
            name="plataforma_exige_pedido",
        ),
        CheckConstraint(
            "CASE WHEN tipo IN ('estorno', 'vencimento') THEN id_movimento_original IS NOT NULL "
            "WHEN tipo = 'ajuste' THEN true ELSE id_movimento_original IS NULL END",
            name="original_do_tipo",
        ),
        CheckConstraint("tipo <> 'ajuste' OR motivo IS NOT NULL", name="ajuste_exige_motivo"),
        # extrato do cliente, na ordem
        Index("ix_movimento_pontos_id_cliente_marca", "id_cliente", "marca", "criado_em"),
        Index("ix_movimento_pontos_id_cliente_sem_conta_marca", "id_cliente_sem_conta", "marca", "criado_em"),
        # pontos de um pedido (estorno no cancelamento e na devolução)
        Index("ix_movimento_pontos_id_pedido", "id_pedido"),
    )


# o "agora" de cada cliente em cada marca: saldo, categoria e pontos ganhos no ano. Escrito só pelo
# trigger aplica_movimento_pontos e pelos jobs do pg_cron; a API só lê
class SaldoPontos(Base):
    __tablename__ = "saldo_pontos"

    id_saldo: Mapped[int] = mapped_column(primary_key=True)
    id_cliente: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("usuario.id_usuario", ondelete="RESTRICT"))
    id_cliente_sem_conta: Mapped[int | None] = mapped_column(
        ForeignKey("cliente_sem_conta.id_cliente_sem_conta", ondelete="RESTRICT")
    )
    marca: Mapped[str] = mapped_column(String(10))
    saldo: Mapped[int] = mapped_column(default=0, server_default="0")  # negativo só herdado da migração
    # sobe na hora em que os pontos ganhos no ano passam do limite; só cai na virada do ano (K4c)
    categoria: Mapped[str] = mapped_column(String(10), default="bronze", server_default="bronze")
    categoria_desde: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ano_referencia: Mapped[int] = mapped_column()  # ano dos pontos ganhos, no horário de Brasília
    ganhos_no_ano: Mapped[int] = mapped_column(default=0, server_default="0")
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(UM_DONO, name="um_dono"),
        CheckConstraint(f"marca IN {MARCAS}", name="marca_valida"),
        CheckConstraint(f"categoria IN {CATEGORIAS}", name="categoria_valida"),
        # um saldo por cliente em cada marca
        Index("uq_saldo_pontos_id_cliente_marca", "id_cliente", "marca", unique=True,
              postgresql_where=text("id_cliente IS NOT NULL")),
        Index("uq_saldo_pontos_id_cliente_sem_conta_marca", "id_cliente_sem_conta", "marca", unique=True,
              postgresql_where=text("id_cliente_sem_conta IS NOT NULL")),
    )


# o que sobra de cada entrada de pontos: o resgate gasta primeiro o mais antigo (FIFO), o valor em R$ é
# o da época em que o ponto foi ganho (K3) e o vencimento leva só o que sobrou (K4a). Escrito só pelo
# trigger e pelos jobs; a API só lê
class LotePontos(Base):
    __tablename__ = "lote_pontos"

    id_movimento: Mapped[int] = mapped_column(  # a entrada que criou o lote
        ForeignKey("movimento_pontos.id_movimento", ondelete="RESTRICT"), primary_key=True
    )
    id_cliente: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("usuario.id_usuario", ondelete="RESTRICT"))
    id_cliente_sem_conta: Mapped[int | None] = mapped_column(
        ForeignKey("cliente_sem_conta.id_cliente_sem_conta", ondelete="RESTRICT")
    )
    marca: Mapped[str] = mapped_column(String(10))
    ganho_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valor_ponto: Mapped[Decimal] = mapped_column(Numeric(6, 4))  # o da regra na data em que foi ganho
    pontos: Mapped[int] = mapped_column()
    restante: Mapped[int] = mapped_column()
    vence_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # vazio: ainda sem validade

    __table_args__ = (
        CheckConstraint(UM_DONO, name="um_dono"),
        CheckConstraint("0 <= restante AND restante <= pontos", name="restante_valido"),
        # FIFO: os lotes com sobra de cada cliente, do mais antigo para o mais novo
        Index("ix_lote_pontos_id_cliente_marca", "id_cliente", "marca", "ganho_em",
              postgresql_where=text("restante > 0")),
        Index("ix_lote_pontos_id_cliente_sem_conta_marca", "id_cliente_sem_conta", "marca", "ganho_em",
              postgresql_where=text("restante > 0")),
        # job do vencimento
        Index("ix_lote_pontos_vence_em", "vence_em", postgresql_where=text("restante > 0")),
    )


# de quais lotes saiu cada saída de pontos; a devolução (estorno do resgate) volta para os mesmos lotes,
# com a época e o valor deles (pontos negativos). Nunca alterado (trigger bloqueia_alteracao)
class ConsumoPontos(Base):
    __tablename__ = "consumo_pontos"

    id_consumo: Mapped[int] = mapped_column(primary_key=True)
    id_movimento: Mapped[int] = mapped_column(ForeignKey("movimento_pontos.id_movimento", ondelete="RESTRICT"))
    id_lote: Mapped[int] = mapped_column(ForeignKey("lote_pontos.id_movimento", ondelete="RESTRICT"))
    pontos: Mapped[int] = mapped_column()  # positivo: saiu do lote; negativo: voltou para ele

    __table_args__ = (
        CheckConstraint("pontos <> 0", name="pontos_diferente_de_zero"),
        Index("ix_consumo_pontos_id_movimento", "id_movimento"),
    )
