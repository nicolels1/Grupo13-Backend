import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint, Uuid, func, text,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base


class CategoriaProduto(Base):
    __tablename__ = "categoria_produto"

    id_categoria: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(100), unique=True)
    ativo: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    # foto do carrossel da página inicial (opcional): caminho no Storage, não URL
    caminho_imagem: Mapped[str | None] = mapped_column(String(500))


# slide do carrossel do topo da página inicial: conteúdo da loja que quem gerencia o catálogo troca
# pelo interno, sem mexer no código. Sem foto, o slide aparece num bloco de cor
class Destaque(Base):
    __tablename__ = "destaque"

    id_destaque: Mapped[int] = mapped_column(primary_key=True)
    titulo: Mapped[str] = mapped_column(String(80))
    texto: Mapped[str] = mapped_column(String(200))
    rotulo_botao: Mapped[str] = mapped_column(String(40))
    # só endereço da própria loja (começa com /): o botão não leva o cliente para fora do site
    link: Mapped[str] = mapped_column(String(300))
    caminho_imagem: Mapped[str | None] = mapped_column(String(500))
    ordem: Mapped[int] = mapped_column()
    ativo: Mapped[bool] = mapped_column(default=True, server_default=text("true"))

    __table_args__ = (
        CheckConstraint("link LIKE '/%' AND link NOT LIKE '//%'", name="link_interno"),
        CheckConstraint("ordem >= 1", name="ordem_positiva"),
    )


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

    # filtro por categoria na vitrine
    __table_args__ = (Index("ix_produto_id_categoria", "id_categoria"),)


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

    __table_args__ = (
        CheckConstraint("ordem >= 1", name="ordem_positiva"),
        # fotos dos produtos em toda página da vitrine e na foto do item do pedido
        Index("ix_imagem_produto_id_produto", "id_produto"),
    )


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
