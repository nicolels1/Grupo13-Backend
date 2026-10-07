import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, Text, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base


class Pedido(Base):
    __tablename__ = "pedido"

    id_pedido: Mapped[int] = mapped_column(primary_key=True)
    codigo_venda: Mapped[str] = mapped_column(String(20), unique=True)
    id_cliente: Mapped[uuid.UUID | None] = mapped_column(  # vazio em venda física sem CPF
        Uuid, ForeignKey("usuario.id_usuario", ondelete="RESTRICT")
    )
    id_unidade: Mapped[int] = mapped_column(ForeignKey("unidade.id_unidade", ondelete="RESTRICT"))
    id_registrado_por: Mapped[uuid.UUID | None] = mapped_column(  # funcionário da venda física
        Uuid, ForeignKey("usuario.id_usuario", ondelete="RESTRICT")
    )
    # CPF informado na venda física de quem ainda não tem conta; o cadastro pelo site liga o pedido (ADR 0014)
    cpf_nota: Mapped[str | None] = mapped_column(String(11))
    canal: Mapped[str] = mapped_column(String(20))
    modalidade: Mapped[str | None] = mapped_column(String(20))  # só na venda online
    status: Mapped[str] = mapped_column(String(30))
    motivo_cancelamento: Mapped[str | None] = mapped_column(String(30))
    id_cancelado_por: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("usuario.id_usuario", ondelete="RESTRICT")
    )
    justificativa_cancelamento: Mapped[str | None] = mapped_column(Text)
    devolucao: Mapped[str] = mapped_column(String(10), default="nenhuma", server_default="nenhuma")
    valor_frete: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0, server_default="0")
    valor_total: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    pronto_retirada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reserva_expira_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pago_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enviado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    entregue_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("canal IN ('loja_fisica', 'online')", name="canal_valido"),
        CheckConstraint("modalidade IN ('entrega', 'retirada')", name="modalidade_valida"),
        CheckConstraint(
            "status IN ('aguardando_pagamento', 'pago', 'enviado', 'pronto_para_retirada', 'entregue', 'cancelado')",
            name="status_valido",
        ),
        CheckConstraint(
            "motivo_cancelamento IN ('cliente', 'reserva_vencida', 'retirada_vencida', 'equipe')",
            name="motivo_cancelamento_valido",
        ),
        CheckConstraint("devolucao IN ('nenhuma', 'parcial', 'total')", name="devolucao_valida"),
        # venda online exige conta e modalidade; venda física não tem modalidade
        CheckConstraint(
            "(canal = 'online') = (modalidade IS NOT NULL)", name="modalidade_so_online"
        ),
        CheckConstraint("canal <> 'online' OR id_cliente IS NOT NULL", name="online_exige_cliente"),
        CheckConstraint(
            "(status = 'cancelado') = (motivo_cancelamento IS NOT NULL)", name="cancelado_exige_motivo"
        ),
        CheckConstraint(
            "motivo_cancelamento IS DISTINCT FROM 'equipe' "
            "OR (id_cancelado_por IS NOT NULL AND justificativa_cancelamento IS NOT NULL)",
            name="cancelamento_equipe_exige_autor",
        ),
        CheckConstraint("valor_frete >= 0 AND valor_total >= 0", name="valores_nao_negativos"),
        CheckConstraint("cpf_nota IS NULL OR canal = 'loja_fisica'", name="cpf_nota_so_loja_fisica"),
        CheckConstraint("cpf_nota ~ '^[0-9]{11}$'", name="cpf_nota_so_digitos"),
        # busca dos pedidos ainda sem conta no cadastro pelo site
        Index("ix_pedido_cpf_nota_sem_cliente", "cpf_nota", postgresql_where=text("id_cliente IS NULL")),
    )


class ItemPedido(Base):
    __tablename__ = "item_pedido"

    id_item: Mapped[int] = mapped_column(primary_key=True)
    id_pedido: Mapped[int] = mapped_column(ForeignKey("pedido.id_pedido", ondelete="RESTRICT"))
    id_variante: Mapped[int] = mapped_column(ForeignKey("variante.id_variante", ondelete="RESTRICT"))
    quantidade: Mapped[int] = mapped_column()
    preco_unitario: Mapped[Decimal] = mapped_column(Numeric(10, 2))  # preço do momento da compra

    __table_args__ = (
        CheckConstraint("quantidade > 0", name="quantidade_positiva"),
        CheckConstraint("preco_unitario >= 0", name="preco_nao_negativo"),
    )


# estorno é um lançamento próprio ligado ao pagamento original (ADR 0003)
class Pagamento(Base):
    __tablename__ = "pagamento"

    id_pagamento: Mapped[int] = mapped_column(primary_key=True)
    id_pedido: Mapped[int] = mapped_column(ForeignKey("pedido.id_pedido", ondelete="RESTRICT"))
    tipo: Mapped[str] = mapped_column(String(20))
    id_pagamento_original: Mapped[int | None] = mapped_column(
        ForeignKey("pagamento.id_pagamento", ondelete="RESTRICT")
    )
    id_chamado: Mapped[int | None] = mapped_column(ForeignKey("chamado.id_chamado", ondelete="RESTRICT"))
    # estorno: de onde veio e, no balcão, quem registrou e em que loja (ADR 0015)
    origem: Mapped[str | None] = mapped_column(String(20))
    id_registrado_por: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("usuario.id_usuario", ondelete="RESTRICT")
    )
    id_unidade: Mapped[int | None] = mapped_column(ForeignKey("unidade.id_unidade", ondelete="RESTRICT"))
    metodo: Mapped[str] = mapped_column(String(20))
    id_transacao_gateway: Mapped[str | None] = mapped_column(String(100), unique=True)
    valor: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    status: Mapped[str] = mapped_column(String(20))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("tipo IN ('pagamento', 'estorno')", name="tipo_valido"),
        CheckConstraint("metodo IN ('pix', 'cartao_credito', 'cartao_debito', 'dinheiro')", name="metodo_valido"),
        CheckConstraint(
            "status IN ('pendente', 'aprovado', 'recusado')", name="status_valido"
        ),
        CheckConstraint("valor > 0", name="valor_positivo"),
        CheckConstraint(
            "(tipo = 'estorno') = (id_pagamento_original IS NOT NULL)", name="estorno_exige_original"
        ),
        CheckConstraint("tipo = 'estorno' OR id_chamado IS NULL", name="chamado_so_em_estorno"),
        CheckConstraint("origem IN ('cancelamento', 'atendimento', 'balcao')", name="origem_valida"),
        CheckConstraint("(tipo = 'estorno') = (origem IS NOT NULL)", name="origem_so_em_estorno"),
        CheckConstraint(
            "origem IS DISTINCT FROM 'balcao' OR (id_registrado_por IS NOT NULL AND id_unidade IS NOT NULL)",
            name="estorno_balcao_exige_funcionario_e_unidade",
        ),
        CheckConstraint(
            "origem IS DISTINCT FROM 'atendimento' OR id_chamado IS NOT NULL",
            name="estorno_atendimento_exige_chamado",
        ),
    )


# cópia do endereço no pedido de entrega em casa
class EnderecoEntrega(Base):
    __tablename__ = "endereco_entrega"

    id_pedido: Mapped[int] = mapped_column(
        ForeignKey("pedido.id_pedido", ondelete="RESTRICT"), primary_key=True
    )
    rua: Mapped[str] = mapped_column(String(200))
    numero: Mapped[str] = mapped_column(String(20))
    complemento: Mapped[str | None] = mapped_column(String(100))
    bairro: Mapped[str] = mapped_column(String(100))
    cidade: Mapped[str] = mapped_column(String(100))
    uf: Mapped[str] = mapped_column(String(2))
    cep: Mapped[str] = mapped_column(String(8))


# endereços salvos: o cliente pode apagar (o pedido guarda a própria cópia)
class EnderecoCliente(Base):
    __tablename__ = "endereco_cliente"

    id_endereco: Mapped[int] = mapped_column(primary_key=True)
    id_cliente: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("usuario.id_usuario", ondelete="RESTRICT")
    )
    rua: Mapped[str] = mapped_column(String(200))
    numero: Mapped[str] = mapped_column(String(20))
    complemento: Mapped[str | None] = mapped_column(String(100))
    bairro: Mapped[str] = mapped_column(String(100))
    cidade: Mapped[str] = mapped_column(String(100))
    uf: Mapped[str] = mapped_column(String(2))
    cep: Mapped[str] = mapped_column(String(8))
