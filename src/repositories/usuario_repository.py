import uuid

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from src.models.contas import ModeloAcesso, Usuario


def id_modelo_admin(db: Session) -> int | None:
    return db.scalar(select(ModeloAcesso.id_modelo).where(ModeloAcesso.eh_admin))


def email_em_uso(db: Session, email: str) -> bool:
    return db.scalar(select(Usuario.id_usuario).where(Usuario.email == email)) is not None


# id do login no Supabase Auth com esse e-mail, se já existir
def buscar_login(db: Session, email: str) -> uuid.UUID | None:
    return db.scalar(text("SELECT id FROM auth.users WHERE lower(email) = :email"), {"email": email})


def login_confirmado(db: Session, id_usuario: uuid.UUID) -> bool:
    consulta = text("SELECT email_confirmed_at IS NOT NULL FROM auth.users WHERE id = :id")
    return bool(db.scalar(consulta, {"id": id_usuario}))


def cpf_em_uso(db: Session, cpf: str) -> bool:
    return db.scalar(select(Usuario.id_usuario).where(Usuario.cpf == cpf)) is not None


# funcionário entra só com e-mail; o login por CPF é só de cliente
def buscar_cliente_por_cpf(db: Session, cpf: str) -> Usuario | None:
    return db.scalar(select(Usuario).where(Usuario.cpf == cpf, Usuario.tipo_conta == "cliente"))
