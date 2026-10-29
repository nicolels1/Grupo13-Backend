import uuid
from datetime import datetime
from sqlalchemy import (
    CheckConstraint, Column, DateTime, ForeignKey, Index, String, Table, Uuid, func, text
)
from sqlalchemy.orm import Mapped, mapped_column
from src.database.base import Base

auth_users = Table("users", Base.metadata, Column("id", Uuid, primary_key=True), schema="auth")

class Usuario(Base):
    __tablename__ = "usuario"

    id_usuario: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("auth.users.id", ondelete="RESTRICT"), primary_key=True
    )
    nome: Mapped[str] = mapped_column(String(150))
    email: Mapped[str] = mapped_column(String(255), unique=True)
    cpf: Mapped[str | None] = mapped_column(String(11), unique=True)
    tipo_conta: Mapped[str] = mapped_column(String(20))
    status_conta: Mapped[str] = mapped_column(String(30))
    id_modelo_acesso: Mapped[int | None] = mapped_column(
        ForeignKey("modelo_acesso.id_modelo", ondelete="RESTRICT")
    )
    id_unidade: Mapped[int | None] = mapped_column(
        ForeignKey("unidade.id_unidade", ondelete="RESTRICT")
    )
    # e-mail antes de os pontos vencerem: só com aceite (vazio = não aceitou). O assistente oferece uma vez
    # por conta; quem recusou ainda pode aceitar pela conta
    lembrete_pontos_aceito_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lembrete_pontos_oferecido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("tipo_conta IN ('interna', 'cliente')", name="tipo_conta_valido"),
        CheckConstraint(
            "status_conta IN ('pendente_ativacao', 'ativa', 'inativa')", name="status_conta_valido"
        ),
        CheckConstraint("tipo_conta <> 'cliente' OR cpf IS NOT NULL", name="cliente_exige_cpf"),
        CheckConstraint(
            "tipo_conta <> 'interna' OR id_modelo_acesso IS NOT NULL", name="interna_exige_modelo"
        ),
    )


# cliente que nunca criou conta, trazido pela migração da Vulto (P2): pedidos, chamados e pontos
# apontam para ele até a pessoa se cadastrar pelo site com o mesmo CPF ou e-mail, quando o histórico
# passa para a conta e id_usuario guarda qual (ADR 0016). Não é uma conta: não tem login nem senha
class ClienteSemConta(Base):
    __tablename__ = "cliente_sem_conta"

    id_cliente_sem_conta: Mapped[int] = mapped_column(primary_key=True)
    cpf: Mapped[str | None] = mapped_column(String(11), unique=True)
    # e-mail só identifica quem não tem CPF: e-mails de família são usados por mais de um CPF (P2)
    email: Mapped[str | None] = mapped_column(String(255))
    nome: Mapped[str | None] = mapped_column(String(150))  # quem só aparece nos pontos não tem nome (K8)
    # só dígitos; fica sem DDD quando não deu para completar (K9a)
    telefone: Mapped[str | None] = mapped_column(String(11))
    aceita_mensagem: Mapped[bool | None] = mapped_column()  # vazio = não informado (K9b)
    origem: Mapped[str] = mapped_column(String(20))
    id_usuario: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("usuario.id_usuario", ondelete="RESTRICT")
    )
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("cpf IS NOT NULL OR email IS NOT NULL", name="cpf_ou_email"),
        CheckConstraint("cpf ~ '^[0-9]{11}$'", name="cpf_so_digitos"),
        CheckConstraint("telefone ~ '^[0-9]{8,11}$'", name="telefone_so_digitos"),
        CheckConstraint("origem IN ('migracao_vulto')", name="origem_valida"),
        # e-mail único entre quem não tem CPF; é também a busca do cadastro pelo e-mail
        Index(
            "uq_cliente_sem_conta_email_sem_cpf", func.lower(email), unique=True,
            postgresql_where=text("cpf IS NULL"),
        ),
    )

class ModeloAcesso(Base):
    __tablename__ = "modelo_acesso"

    id_modelo: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(100), unique=True)
    eh_admin: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    ativo: Mapped[bool] = mapped_column(default=True, server_default=text("true"))

    # no máximo um modelo com eh_admin verdadeiro
    __table_args__ = (
        Index("uq_modelo_acesso_admin_unico", "eh_admin", unique=True,
              postgresql_where=text("eh_admin")),
    )

class Permissao(Base):
    __tablename__ = "permissao"

    id_permissao: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(100), unique=True)
    descricao: Mapped[str] = mapped_column(String(150))

class ModeloPermissao(Base):
    __tablename__ = "modelo_permissao"

    id_modelo: Mapped[int] = mapped_column(
        ForeignKey("modelo_acesso.id_modelo", ondelete="RESTRICT"), primary_key=True
    )
    id_permissao: Mapped[int] = mapped_column(
        ForeignKey("permissao.id_permissao", ondelete="RESTRICT"), primary_key=True
    )

class UsuarioPermissaoExcecao(Base):
    __tablename__ = "usuario_permissao_excecao"

    id_usuario: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("usuario.id_usuario", ondelete="RESTRICT"), primary_key=True
    )
    id_permissao: Mapped[int] = mapped_column(
        ForeignKey("permissao.id_permissao", ondelete="RESTRICT"), primary_key=True
    )
    efeito: Mapped[str] = mapped_column(String(20))

    __table_args__ = (
        CheckConstraint("efeito IN ('acrescentar', 'retirar')", name="efeito_valido"),
    )
