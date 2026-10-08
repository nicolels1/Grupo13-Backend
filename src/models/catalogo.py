import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base


class CategoriaProduto(Base):
    __tablename__ = "categoria_produto"

    id_categoria: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(100), unique=True)
    ativo: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    # foto do carrossel da página inicial (opcional): caminho no Storage, não URL
    caminho_imagem: Mapped[str | None] = mapped_column(String(500))


class Produto(Base):
    __tablename__ = "produto"

    id_produto: Mapped[int] = mapped_column(primary_key=True)
    id_categoria: Mapped[int] = mapped_column(
        ForeignKey("categoria_produto.id_categoria", ondelete="RESTRICT")
    )
    nome: Mapped[str] = mapped_column(String(150))
    descricao_tecnica: Mapped[str] = mapped_column(Text)
    descricao_cliente: Mapped[str] = mapped_column(Text)
    ativo: Mapped[bool] = mapped_column(default=True, server_default=text("true"))


class Variante(Base):
    __tablename__ = "variante"

    id_variante: Mapped[int] = mapped_column(primary_key=True)
    id_produto: Mapped[int] = mapped_column(ForeignKey("produto.id_produto", ondelete="RESTRICT"))
    sku: Mapped[str] = mapped_column(String(50), unique=True)
    cor: Mapped[str] = mapped_column(String(50))  # peça sem cor usa "Única"
    tamanho: Mapped[str] = mapped_column(String(20))  # peça sem tamanho usa "U"
    preco: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    ativo: Mapped[bool] = mapped_column(default=True, server_default=text("true"))

    __table_args__ = (
        UniqueConstraint("id_produto", "cor", "tamanho"),
        CheckConstraint("preco >= 0", name="preco_nao_negativo"),
    )


class ImagemProduto(Base):
    __tablename__ = "imagem_produto"

    id_imagem: Mapped[int] = mapped_column(primary_key=True)
    id_produto: Mapped[int] = mapped_column(ForeignKey("produto.id_produto", ondelete="RESTRICT"))
    cor: Mapped[str | None] = mapped_column(String(50))  # sem cor: vale para todas
    caminho_arquivo: Mapped[str] = mapped_column(String(500))  # caminho no Storage, não URL
    ordem: Mapped[int] = mapped_column()

    __table_args__ = (CheckConstraint("ordem >= 1", name="ordem_positiva"),)


# nunca editado nem apagado (trigger na migration)
class HistoricoPreco(Base):
    __tablename__ = "historico_preco"

    id_historico_preco: Mapped[int] = mapped_column(primary_key=True)
    id_variante: Mapped[int] = mapped_column(ForeignKey("variante.id_variante", ondelete="RESTRICT"))
    preco_anterior: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))  # vazio na criação
    preco_novo: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    id_alterado_por: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("usuario.id_usuario", ondelete="RESTRICT")
    )
    alterado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (CheckConstraint("preco_novo >= 0", name="preco_nao_negativo"),)
