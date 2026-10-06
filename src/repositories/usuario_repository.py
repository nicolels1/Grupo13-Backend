import uuid

from sqlalchemy import Select, func, or_, select, text
from sqlalchemy.orm import Session

from src.models.contas import ModeloAcesso, Usuario
from src.models.estoque import Unidade


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


# ---------- Gestão: lista de contas ----------

def consulta_usuarios(tipo_conta=None, status_conta=None, id_modelo_acesso=None, id_unidade=None, busca=None) -> Select:
    consulta = (
        select(
            Usuario.id_usuario, Usuario.nome, Usuario.email, Usuario.tipo_conta, Usuario.status_conta,
            Usuario.id_modelo_acesso, ModeloAcesso.nome.label("modelo_acesso"), Usuario.id_unidade,
            Unidade.nome.label("unidade"), Usuario.criado_em,
        )
        .outerjoin(ModeloAcesso, ModeloAcesso.id_modelo == Usuario.id_modelo_acesso)
        .outerjoin(Unidade, Unidade.id_unidade == Usuario.id_unidade)
        .order_by(Usuario.nome, Usuario.email)
    )
    filtros = []
    for coluna, valor in [
        (Usuario.tipo_conta, tipo_conta), (Usuario.status_conta, status_conta),
        (Usuario.id_modelo_acesso, id_modelo_acesso), (Usuario.id_unidade, id_unidade),
    ]:
        if valor is not None:
            filtros.append(coluna == valor)
    if busca:
        padrao = f"%{busca}%"
        filtros.append(or_(Usuario.nome.ilike(padrao), Usuario.email.ilike(padrao)))
    return consulta.where(*filtros)


def listar_usuarios(db: Session, limit: int, offset: int, **filtros) -> tuple[list, int]:
    consulta = consulta_usuarios(**filtros)
    total = db.scalar(select(func.count()).select_from(consulta.order_by(None).subquery()))
    linhas = db.execute(consulta.limit(limit).offset(offset)).mappings().all()
    return list(linhas), total
