import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base

CANAIS = "('loja_fisica', 'online')"


class Unidade(Base):
    __tablename__ = "unidade"

    id_unidade: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(100), unique=True)
    tipo: Mapped[str] = mapped_column(String(10))
    despacha_online: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    rua: Mapped[str] = mapped_column(String(200))
    numero: Mapped[str] = mapped_column(String(20))
    complemento: Mapped[str | None] = mapped_column(String(100))
    bairro: Mapped[str] = mapped_column(String(100))
    cidade: Mapped[str] = mapped_column(String(100))
    uf: Mapped[str] = mapped_column(String(2))
    cep: Mapped[str] = mapped_column(String(8))
    ativo: Mapped[bool] = mapped_column(default=True, server_default=text("true"))

    __table_args__ = (
        CheckConstraint("tipo IN ('loja', 'cd')", name="tipo_valido"),
        # o CD sempre despacha online; loja só se marcada
        CheckConstraint("tipo <> 'cd' OR despacha_online", name="cd_despacha_online"),
    )


# saldo atualizado só pelo trigger a cada movimentação (ADR 0005)
class Estoque(Base):
    __tablename__ = "estoque"

    id_variante: Mapped[int] = mapped_column(
        ForeignKey("variante.id_variante", ondelete="RESTRICT"), primary_key=True
    )
    id_unidade: Mapped[int] = mapped_column(
        ForeignKey("unidade.id_unidade", ondelete="RESTRICT"), primary_key=True
    )
    canal: Mapped[str] = mapped_column(String(20), primary_key=True)
    quantidade: Mapped[int] = mapped_column(default=0, server_default="0")
    quantidade_reservada: Mapped[int] = mapped_column(default=0, server_default="0")
    estoque_minimo: Mapped[int | None] = mapped_column()
    minimo_alterado_por: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("usuario.id_usuario", ondelete="RESTRICT")
    )
    minimo_alterado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(f"canal IN {CANAIS}", name="canal_valido"),
        CheckConstraint("quantidade >= 0", name="quantidade_nao_negativa"),
        CheckConstraint("quantidade_reservada >= 0", name="reservada_nao_negativa"),
        CheckConstraint("quantidade_reservada <= quantidade", name="reservada_ate_quantidade"),
        CheckConstraint("canal = 'online' OR quantidade_reservada = 0", name="reserva_so_online"),
        CheckConstraint("estoque_minimo IS NULL OR estoque_minimo >= 0", name="minimo_nao_negativo"),
    )


class Transferencia(Base):
    __tablename__ = "transferencia"

    id_transferencia: Mapped[int] = mapped_column(primary_key=True)
    id_unidade_origem: Mapped[int] = mapped_column(ForeignKey("unidade.id_unidade", ondelete="RESTRICT"))
    id_unidade_destino: Mapped[int] = mapped_column(ForeignKey("unidade.id_unidade", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(String(20))
    id_solicitante: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("usuario.id_usuario", ondelete="RESTRICT")
    )
    id_enviado_por: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("usuario.id_usuario", ondelete="RESTRICT")
    )
    id_recebido_por: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("usuario.id_usuario", ondelete="RESTRICT")
    )
    id_cancelado_por: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("usuario.id_usuario", ondelete="RESTRICT")
    )
    motivo_cancelamento: Mapped[str | None] = mapped_column(Text)
    solicitada_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    enviada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recebida_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('solicitada', 'enviada', 'recebida', 'cancelada')", name="status_valido"
        ),
        CheckConstraint("id_unidade_origem <> id_unidade_destino", name="origem_diferente_destino"),
        CheckConstraint(
            "status <> 'cancelada' OR (motivo_cancelamento IS NOT NULL AND id_cancelado_por IS NOT NULL)",
            name="cancelada_exige_motivo",
        ),
    )


class ItemTransferencia(Base):
    __tablename__ = "item_transferencia"

    id_item_transferencia: Mapped[int] = mapped_column(primary_key=True)
    id_transferencia: Mapped[int] = mapped_column(
        ForeignKey("transferencia.id_transferencia", ondelete="RESTRICT")
    )
    id_variante: Mapped[int] = mapped_column(ForeignKey("variante.id_variante", ondelete="RESTRICT"))
    canal_saida: Mapped[str] = mapped_column(String(20))
    canal_entrada: Mapped[str] = mapped_column(String(20))
    quantidade_solicitada: Mapped[int] = mapped_column()
    quantidade_enviada: Mapped[int | None] = mapped_column()
    quantidade_recebida: Mapped[int | None] = mapped_column()

    __table_args__ = (
        CheckConstraint(f"canal_saida IN {CANAIS}", name="canal_saida_valido"),
        CheckConstraint(f"canal_entrada IN {CANAIS}", name="canal_entrada_valido"),
        CheckConstraint("quantidade_solicitada > 0", name="solicitada_positiva"),
        CheckConstraint("quantidade_enviada IS NULL OR quantidade_enviada >= 0", name="enviada_nao_negativa"),
        CheckConstraint(
            "quantidade_recebida IS NULL OR quantidade_recebida >= 0", name="recebida_nao_negativa"
        ),
        CheckConstraint(
            "quantidade_recebida IS NULL OR quantidade_recebida <= quantidade_enviada",
            name="recebida_ate_enviada",
        ),
    )


# nunca editada nem apagada; cada insert atualiza ESTOQUE pelo trigger (ADR 0005)
class MovimentacaoEstoque(Base):
    __tablename__ = "movimentacao_estoque"

    id_movimentacao: Mapped[int] = mapped_column(primary_key=True)
    id_variante: Mapped[int] = mapped_column(ForeignKey("variante.id_variante", ondelete="RESTRICT"))
    id_unidade: Mapped[int] = mapped_column(ForeignKey("unidade.id_unidade", ondelete="RESTRICT"))
    canal: Mapped[str] = mapped_column(String(20))
    id_usuario: Mapped[uuid.UUID | None] = mapped_column(  # vazio nas automáticas
        Uuid, ForeignKey("usuario.id_usuario", ondelete="RESTRICT")
    )
    tipo: Mapped[str] = mapped_column(String(30))
    quantidade: Mapped[int] = mapped_column()  # com sinal: positiva entra, negativa sai
    motivo: Mapped[str | None] = mapped_column(Text)
    id_pedido: Mapped[int | None] = mapped_column(ForeignKey("pedido.id_pedido", ondelete="RESTRICT"))
    id_transferencia: Mapped[int | None] = mapped_column(
        ForeignKey("transferencia.id_transferencia", ondelete="RESTRICT")
    )
    id_chamado: Mapped[int | None] = mapped_column(ForeignKey("chamado.id_chamado", ondelete="RESTRICT"))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(f"canal IN {CANAIS}", name="canal_valido"),
        CheckConstraint(
            "tipo IN ('saldo_inicial', 'recebimento', 'avaria', 'perda', 'ajuste', 'venda', "
            "'retorno_cancelamento', 'devolucao', 'saida_troca', 'saida_transferencia', "
            "'entrada_transferencia', 'saida_realocacao', 'entrada_realocacao')",
            name="tipo_valido",
        ),
        CheckConstraint("quantidade <> 0", name="quantidade_nao_zero"),
        CheckConstraint(
            "tipo NOT IN ('avaria', 'perda', 'ajuste') OR motivo IS NOT NULL", name="motivo_obrigatorio"
        ),
        # troca e devolução podem ter pedido e chamado juntos (feitas pelo Atendimento, ADR 0015)
        CheckConstraint(
            "num_nonnulls(id_pedido, id_transferencia, id_chamado) <= 1 "
            "OR (tipo IN ('devolucao', 'saida_troca') AND id_transferencia IS NULL)",
            name="no_maximo_uma_origem",
        ),
        # troca e devolução sempre ligadas ao pedido, com chamado (Atendimento) ou funcionário (balcão)
        CheckConstraint(
            "tipo NOT IN ('devolucao', 'saida_troca') "
            "OR (id_pedido IS NOT NULL AND (id_chamado IS NOT NULL OR id_usuario IS NOT NULL))",
            name="troca_devolucao_com_pedido",
        ),
    )
