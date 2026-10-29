import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base


class Chamado(Base):
    __tablename__ = "chamado"

    id_chamado: Mapped[int] = mapped_column(primary_key=True)
    # cliente com conta ou, no atendimento migrado, cliente sem conta (P2): o cadastro passa para a conta
    id_cliente: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("usuario.id_usuario", ondelete="RESTRICT")
    )
    id_cliente_sem_conta: Mapped[int | None] = mapped_column(
        ForeignKey("cliente_sem_conta.id_cliente_sem_conta", ondelete="RESTRICT")
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
    # sugerida pela IA ao abrir o chamado (Entrega 4), separada da prioridade: a IA nunca decide, a pessoa confirma.
    # Comparar as duas mede quantas vezes a IA acertou. Só a equipe vê
    prioridade_sugerida: Mapped[str | None] = mapped_column(String(10))
    motivo_sugerido: Mapped[str | None] = mapped_column(String(200))
    sugerida_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    motivo_encerramento: Mapped[str | None] = mapped_column(String(20))
    # atendimento migrado da Vulto (P6): por onde a pessoa escreveu e o contato que ela usou.
    # telegram: aberto pelo assistente do cliente no bot do Telegram
    canal_externo: Mapped[str | None] = mapped_column(String(10))
    contato_externo: Mapped[str | None] = mapped_column(String(255))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    assumido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    concluido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "categoria IN ('entrega', 'troca_devolucao', 'estorno', 'duvida', 'pagamento', 'outros')",
            name="categoria_valida",
        ),
        CheckConstraint("status IN ('aberto', 'em_andamento', 'concluido')", name="status_valido"),
        CheckConstraint("prioridade IN ('baixa', 'media', 'alta')", name="prioridade_valida"),
        CheckConstraint("prioridade_sugerida IN ('baixa', 'media', 'alta')", name="prioridade_sugerida_valida"),
        CheckConstraint("(prioridade_sugerida IS NULL) = (sugerida_em IS NULL)", name="sugestao_com_data"),
        CheckConstraint(
            "motivo_encerramento IN ('resolvido', 'desistencia', 'sem_resposta')",
            name="motivo_encerramento_valido",
        ),
        CheckConstraint(
            "(status = 'concluido') = (motivo_encerramento IS NOT NULL)", name="concluido_exige_motivo"
        ),
        # sem cliente, só o atendimento migrado da Vulto em que ninguém foi achado (A5–A7)
        CheckConstraint(
            "num_nonnulls(id_cliente, id_cliente_sem_conta) = 1 OR "
            "(id_cliente IS NULL AND id_cliente_sem_conta IS NULL AND canal_externo IS NOT NULL)",
            name="um_cliente_ou_externo",
        ),
        CheckConstraint(
            "canal_externo IN ('whatsapp', 'instagram', 'email', 'telegram')", name="canal_externo_valido"
        ),
        CheckConstraint("contato_externo IS NULL OR canal_externo IS NOT NULL", name="contato_externo_exige_canal"),
        # "meus chamados" do cliente
        Index("ix_chamado_id_cliente", "id_cliente"),
        # chamados que passam para a conta no cadastro
        Index("ix_chamado_id_cliente_sem_conta", "id_cliente_sem_conta"),
    )


class Mensagem(Base):
    __tablename__ = "mensagem"

    id_mensagem: Mapped[int] = mapped_column(primary_key=True)
    id_chamado: Mapped[int] = mapped_column(ForeignKey("chamado.id_chamado", ondelete="RESTRICT"))
    # sem autor: a fala do cliente do chamado migrado da Vulto, que não tem um usuário (P6); a API sempre grava o autor
    id_autor: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("usuario.id_usuario", ondelete="RESTRICT"))
    # nome de quem falou no atendimento migrado: o cliente e, nas falas da conta "Migração Vulto", o nome original
    nome_autor_externo: Mapped[str | None] = mapped_column(String(150))
    conteudo: Mapped[str | None] = mapped_column(Text)
    anexo_caminho: Mapped[str | None] = mapped_column(String(500))  # Storage privado, não URL
    anexo_nome: Mapped[str | None] = mapped_column(String(255))
    anexo_tamanho: Mapped[int | None] = mapped_column()  # em bytes
    interna: Mapped[bool] = mapped_column(default=False, server_default=text("false"))  # invisível ao cliente
    lida_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("conteudo IS NOT NULL OR anexo_caminho IS NOT NULL", name="texto_ou_anexo"),
        CheckConstraint("id_autor IS NOT NULL OR nome_autor_externo IS NOT NULL", name="autor_ou_nome_externo"),
        # conversa do chamado, mensagens não lidas e marcar como lidas
        Index("ix_mensagem_id_chamado", "id_chamado"),
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

    # histórico de um chamado
    __table_args__ = (Index("ix_historico_chamado_id_chamado", "id_chamado"),)
