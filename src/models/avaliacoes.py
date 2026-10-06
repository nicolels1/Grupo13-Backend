import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base


# uma por item de pedido entregue; publicada na hora, a equipe pode ocultar
class Avaliacao(Base):
    __tablename__ = "avaliacao"

    id_avaliacao: Mapped[int] = mapped_column(primary_key=True)
    id_item_pedido: Mapped[int] = mapped_column(
        ForeignKey("item_pedido.id_item", ondelete="RESTRICT"), unique=True
    )
    nota: Mapped[int] = mapped_column()
    texto: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="publicada", server_default="publicada")
    motivo_ocultacao: Mapped[str | None] = mapped_column(Text)
    id_ocultada_por: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("usuario.id_usuario", ondelete="RESTRICT")
    )
    ocultada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    criada_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    editada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("nota BETWEEN 1 AND 5", name="nota_de_1_a_5"),
        CheckConstraint("status IN ('publicada', 'oculta')", name="status_valido"),
        CheckConstraint(
            "status <> 'oculta' OR (motivo_ocultacao IS NOT NULL AND id_ocultada_por IS NOT NULL "
            "AND ocultada_em IS NOT NULL)",
            name="oculta_exige_motivo",
        ),
    )


class FotoAvaliacao(Base):
    __tablename__ = "foto_avaliacao"

    id_foto: Mapped[int] = mapped_column(primary_key=True)
    id_avaliacao: Mapped[int] = mapped_column(ForeignKey("avaliacao.id_avaliacao", ondelete="RESTRICT"))
    caminho_arquivo: Mapped[str] = mapped_column(String(500))  # caminho no Storage, não URL
    ordem: Mapped[int] = mapped_column()

    # ordem de 1 a 5 e única por avaliação: no máximo 5 fotos
    __table_args__ = (
        CheckConstraint("ordem BETWEEN 1 AND 5", name="ordem_de_1_a_5"),
        UniqueConstraint("id_avaliacao", "ordem"),
    )


class VotoUtil(Base):
    __tablename__ = "voto_util"

    id_avaliacao: Mapped[int] = mapped_column(
        ForeignKey("avaliacao.id_avaliacao", ondelete="RESTRICT"), primary_key=True
    )
    id_cliente: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("usuario.id_usuario", ondelete="RESTRICT"), primary_key=True
    )
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DenunciaAvaliacao(Base):
    __tablename__ = "denuncia_avaliacao"

    id_denuncia: Mapped[int] = mapped_column(primary_key=True)
    id_avaliacao: Mapped[int] = mapped_column(ForeignKey("avaliacao.id_avaliacao", ondelete="RESTRICT"))
    id_cliente: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("usuario.id_usuario", ondelete="RESTRICT"))
    motivo: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pendente", server_default="pendente")
    id_analisada_por: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("usuario.id_usuario", ondelete="RESTRICT")
    )
    analisada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    criada_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("id_avaliacao", "id_cliente"),  # uma denúncia por cliente por avaliação
        CheckConstraint("status IN ('pendente', 'procedente', 'improcedente')", name="status_valido"),
        CheckConstraint(
            "status = 'pendente' OR (id_analisada_por IS NOT NULL AND analisada_em IS NOT NULL)",
            name="analisada_exige_autor",
        ),
    )
