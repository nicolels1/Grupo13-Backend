import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint, Date, DateTime, ForeignKey, ForeignKeyConstraint, Index, String, Text, Uuid, func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base

# whatsapp é o produto; telegram é a demonstração da banca
CANAIS = ("whatsapp", "telegram")
ETAPAS = ("livre", "esperando_cpf", "esperando_codigo", "confirmando_chamado", "em_chamado")
# assunto de cada ferramenta do assistente; fora_do_tema vem do n8n
ASSUNTOS = ("pecas", "carrinho", "estoque", "lojas", "pontos", "pedidos", "chamado", "fora_do_tema")
AVALIACOES = ("positiva", "negativa")
PAPEIS = ("cliente", "assistente")


# resumo anônimo de cada conversa, fonte das métricas do assistente interno (metricas_assistente).
# Nasce no começo da conversa e é preenchido pelas ferramentas; não guarda número, cliente nem texto.
# Sem criado_em: o horário fica só na hora cheia, para não cruzar com o log do n8n. Fica para sempre
class ResumoConversa(Base):
    __tablename__ = "resumo_conversa"

    id_resumo: Mapped[int] = mapped_column(primary_key=True)
    canal: Mapped[str] = mapped_column(String(10))
    iniciada_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # confirmou CPF e código na conversa; quem tem conta e não se identificou fica falso
    autenticou: Mapped[bool] = mapped_column(default=False, server_default="false")
    assuntos: Mapped[list[str]] = mapped_column(ARRAY(String(20)), default=list, server_default="{}")
    virou_chamado: Mapped[bool] = mapped_column(default=False, server_default="false")
    # erro de verdade (rota fora do ar); busca vazia não conta
    ferramenta_falhou: Mapped[bool] = mapped_column(default=False, server_default="false")
    avaliacao: Mapped[str | None] = mapped_column(String(10))  # 👍/👎 opcional

    __table_args__ = (
        CheckConstraint(f"canal IN {CANAIS}", name="canal_valido"),
        CheckConstraint(
            "EXTRACT(MINUTE FROM iniciada_em) = 0 AND EXTRACT(SECOND FROM iniciada_em) = 0", name="hora_cheia"
        ),
        CheckConstraint(f"assuntos <@ ARRAY{list(ASSUNTOS)}::varchar[]", name="assuntos_validos"),
        CheckConstraint(f"avaliacao IN {AVALIACOES}", name="avaliacao_valida"),
    )


# uma pessoa num canal do assistente do cliente: telefone no WhatsApp, chat_id no Telegram.
# A chave vem do canal, nunca do modelo. id_cliente (ou o cliente sem conta, ADR 0016) é a conta
# confirmada pelo código por e-mail na conversa em andamento; vale até confirmado_ate
class ConversaCanal(Base):
    __tablename__ = "conversa_canal"

    canal: Mapped[str] = mapped_column(String(10), primary_key=True)
    id_externo: Mapped[str] = mapped_column(String(100), primary_key=True)
    id_cliente: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("usuario.id_usuario", ondelete="RESTRICT"))
    id_cliente_sem_conta: Mapped[int | None] = mapped_column(
        ForeignKey("cliente_sem_conta.id_cliente_sem_conta", ondelete="RESTRICT")
    )
    confirmado_ate: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    etapa: Mapped[str] = mapped_column(String(30), default="livre", server_default="livre")
    # categoria, assunto, resumo e pedido que a IA montou, guardados até o cliente apertar [Enviar]:
    # o chamado abre só com o que ele viu. Só existe em confirmando_chamado
    rascunho_chamado: Mapped[dict | None] = mapped_column(JSONB)
    id_chamado: Mapped[int | None] = mapped_column(ForeignKey("chamado.id_chamado", ondelete="RESTRICT"))
    # resumo da conversa em andamento; trocado na conversa nova, então o número só fica ligado a ele
    # enquanto a conversa está aberta
    id_resumo_atual: Mapped[int | None] = mapped_column(
        ForeignKey("resumo_conversa.id_resumo", ondelete="RESTRICT")
    )
    ultima_msg_cliente_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # começo da conversa em andamento: ao confirmar a conta, o texto guardado desde aqui passa para ela
    conversa_iniciada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # limite diário de mensagens, no dia de Brasília
    msgs_hoje: Mapped[int] = mapped_column(default=0, server_default="0")
    dia_contagem: Mapped[date | None] = mapped_column(Date)
    bloqueado_ate: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(f"canal IN {CANAIS}", name="canal_valido"),
        CheckConstraint(f"etapa IN {ETAPAS}", name="etapa_valida"),
        CheckConstraint("(etapa = 'confirmando_chamado') = (rascunho_chamado IS NOT NULL)",
                        name="rascunho_so_confirmando"),
        CheckConstraint("id_cliente IS NULL OR id_cliente_sem_conta IS NULL", name="um_dono_so"),
    )


# código de 6 dígitos mandado ao e-mail do cadastro. Guarda só o hash; o CPF digitado não fica.
# CPF sem cadastro também gera uma linha, sem cliente: a resposta é a mesma e nenhum código serve
class CodigoVerificacao(Base):
    __tablename__ = "codigo_verificacao"

    id_codigo: Mapped[int] = mapped_column(primary_key=True)
    canal: Mapped[str] = mapped_column(String(10))
    id_externo: Mapped[str] = mapped_column(String(100))
    id_cliente: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("usuario.id_usuario", ondelete="RESTRICT"))
    id_cliente_sem_conta: Mapped[int | None] = mapped_column(
        ForeignKey("cliente_sem_conta.id_cliente_sem_conta", ondelete="RESTRICT")
    )
    codigo_hash: Mapped[str] = mapped_column(String(64))
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    tentativas: Mapped[int] = mapped_column(default=0, server_default="0")
    # usado (certo), trocado por um novo ou esgotado nas tentativas: não vale mais
    encerrado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        ForeignKeyConstraint(
            ["canal", "id_externo"], ["conversa_canal.canal", "conversa_canal.id_externo"],
            ondelete="CASCADE", name="fk_codigo_verificacao_conversa_canal",
        ),
        CheckConstraint("id_cliente IS NULL OR id_cliente_sem_conta IS NULL", name="um_dono_so"),
        Index("ix_codigo_verificacao_conversa", "canal", "id_externo", "criado_em"),
    )


# texto da conversa, para revisar onde o assistente falhou. Anônimo até o cliente confirmar a conta; aí o
# texto da conversa em andamento passa para ela. CPF e código saem mascarados. O pg_cron apaga o que passou
# de 90 dias (a conversa que virou chamado continua no chamado)
class MensagemConversa(Base):
    __tablename__ = "mensagem_conversa"

    id_mensagem: Mapped[int] = mapped_column(primary_key=True)
    canal: Mapped[str] = mapped_column(String(10))
    id_externo: Mapped[str] = mapped_column(String(100))
    id_cliente: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("usuario.id_usuario", ondelete="RESTRICT"))
    id_cliente_sem_conta: Mapped[int | None] = mapped_column(
        ForeignKey("cliente_sem_conta.id_cliente_sem_conta", ondelete="RESTRICT")
    )
    papel: Mapped[str] = mapped_column(String(10))
    texto: Mapped[str] = mapped_column(Text)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        ForeignKeyConstraint(
            ["canal", "id_externo"], ["conversa_canal.canal", "conversa_canal.id_externo"],
            ondelete="CASCADE", name="fk_mensagem_conversa_conversa_canal",
        ),
        CheckConstraint(f"papel IN {PAPEIS}", name="papel_valido"),
        CheckConstraint("id_cliente IS NULL OR id_cliente_sem_conta IS NULL", name="um_dono_so"),
        Index("ix_mensagem_conversa_conversa", "canal", "id_externo", "criado_em"),
        Index("ix_mensagem_conversa_criado_em", "criado_em"),
    )


# auditoria (CFO): o que o backend entregou ao assistente com dado pessoal, para provar o que foi dito
# ao cliente mesmo que esteja errado. Fica para sempre
class AfirmacaoAssistente(Base):
    __tablename__ = "afirmacao_assistente"

    id_afirmacao: Mapped[int] = mapped_column(primary_key=True)
    canal: Mapped[str] = mapped_column(String(10))
    id_externo: Mapped[str] = mapped_column(String(100))
    id_cliente: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("usuario.id_usuario", ondelete="RESTRICT"))
    id_cliente_sem_conta: Mapped[int | None] = mapped_column(
        ForeignKey("cliente_sem_conta.id_cliente_sem_conta", ondelete="RESTRICT")
    )
    ferramenta: Mapped[str] = mapped_column(String(30))
    dados: Mapped[dict] = mapped_column(JSONB)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("ferramenta IN ('meus_pontos', 'meus_pedidos')", name="ferramenta_valida"),
        CheckConstraint("id_cliente IS NOT NULL OR id_cliente_sem_conta IS NOT NULL", name="tem_dono"),
    )
