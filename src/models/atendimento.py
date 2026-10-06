import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base


class Chamado(Base):
    __tablename__ = "chamado"

    id_chamado: Mapped[int] = mapped_column(primary_key=True)
    id_cliente: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("usuario.id_usuario", ondelete="RESTRICT")
    )
    id_responsavel: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("usuario.id_usuario", ondelete="RESTRICT")
    )
    id_unidade: Mapped[int | None] = mapped_column(ForeignKey("unidade.id_unidade", ondelete="RESTRICT"))
    id_pedido: Mapped[int | None] = mapped_column(ForeignKey("pedido.id_pedido", ondelete="RESTRICT"))
    id_item_pedido: Mapped[int | None] = mapped_column(
        ForeignKey("item_pedido.id_item", ondelete="RESTRICT")
    )
    id_variante: Mapped[int | None] = mapped_column(ForeignKey("variante.id_variante", ondelete="RESTRICT"))
    id_chamado_anterior: Mapped[int | None] = mapped_column(
        ForeignKey("chamado.id_chamado", ondelete="RESTRICT")
    )
    categoria: Mapped[str] = mapped_column(String(30))
    assunto: Mapped[str] = mapped_column(String(200))
    descricao: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="aberto", server_default="aberto")
    prioridade: Mapped[str | None] = mapped_column(String(10))  # definida por quem assume
    motivo_encerramento: Mapped[str | None] = mapped_column(String(20))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    assumido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    concluido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "categoria IN ('entrega', 'troca_devolucao', 'estorno', 'duvida', 'outros')",
            name="categoria_valida",
        ),
        CheckConstraint("status IN ('aberto', 'em_andamento', 'concluido')", name="status_valido"),
        CheckConstraint("prioridade IN ('baixa', 'media', 'alta')", name="prioridade_valida"),
        CheckConstraint(
            "motivo_encerramento IN ('resolvido', 'desistencia', 'sem_resposta')",
            name="motivo_encerramento_valido",
        ),
        CheckConstraint(
            "(status = 'concluido') = (motivo_encerramento IS NOT NULL)", name="concluido_exige_motivo"
        ),
    )


class Mensagem(Base):
    __tablename__ = "mensagem"

    id_mensagem: Mapped[int] = mapped_column(primary_key=True)
    id_chamado: Mapped[int] = mapped_column(ForeignKey("chamado.id_chamado", ondelete="RESTRICT"))
    id_autor: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("usuario.id_usuario", ondelete="RESTRICT"))
    conteudo: Mapped[str | None] = mapped_column(Text)
    anexo_caminho: Mapped[str | None] = mapped_column(String(500))  # Storage privado, não URL
    anexo_nome: Mapped[str | None] = mapped_column(String(255))
    anexo_tamanho: Mapped[int | None] = mapped_column()  # em bytes
    interna: Mapped[bool] = mapped_column(default=False, server_default=text("false"))  # invisível ao cliente
    lida_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("conteudo IS NOT NULL OR anexo_caminho IS NOT NULL", name="texto_ou_anexo"),
    )


# nunca editado nem apagado (trigger na migration)
class HistoricoChamado(Base):
    __tablename__ = "historico_chamado"

    id_historico: Mapped[int] = mapped_column(primary_key=True)
    id_chamado: Mapped[int] = mapped_column(ForeignKey("chamado.id_chamado", ondelete="RESTRICT"))
    id_autor: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("usuario.id_usuario", ondelete="RESTRICT"))
    campo_alterado: Mapped[str] = mapped_column(String(30))
    valor_anterior: Mapped[str | None] = mapped_column(String(255))
    valor_novo: Mapped[str] = mapped_column(String(255))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
