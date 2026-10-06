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
