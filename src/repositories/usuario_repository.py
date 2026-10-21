import uuid

from sqlalchemy import Select, func, or_, select, text
from sqlalchemy.orm import Session

from src.models.contas import ClienteSemConta, ModeloAcesso, Usuario
from src.models.estoque import Unidade


def id_modelo_admin(db: Session) -> int | None:
    return db.scalar(select(ModeloAcesso.id_modelo).where(ModeloAcesso.eh_admin))


def email_em_uso(db: Session, email: str) -> bool:
    return db.scalar(select(Usuario.id_usuario).where(Usuario.email == email)) is not None


# o login fica no Supabase Auth (schema auth), que o usuário restrito da API não lê: as duas
# funções do banco (migration 56799f788354) respondem só o que a API precisa

# id do login no Supabase Auth com esse e-mail, se já existir
def buscar_login(db: Session, email: str) -> uuid.UUID | None:
    return db.scalar(text("SELECT login_por_email(:email)"), {"email": email})


def login_confirmado(db: Session, id_usuario: uuid.UUID) -> bool:
    return bool(db.scalar(text("SELECT login_confirmado(:id)"), {"id": id_usuario}))


def cpf_em_uso(db: Session, cpf: str) -> bool:
    return db.scalar(select(Usuario.id_usuario).where(Usuario.cpf == cpf)) is not None


# funcionário entra só com e-mail; o login por CPF é só de cliente
def buscar_cliente_por_cpf(db: Session, cpf: str) -> Usuario | None:
    return db.scalar(select(Usuario).where(Usuario.cpf == cpf, Usuario.tipo_conta == "cliente"))


# ---------- área Clientes: clientes com conta ----------

# cadastro da Vulto que passou para a conta (ADR 0016); quem tinha um pelo CPF e outro pelo e-mail fica
# com o do CPF, que é o da pessoa (o e-mail pode ser de família, P2)
def _cadastro_da_vulto():
    return (
        select(ClienteSemConta.id_cliente_sem_conta)
        .where(ClienteSemConta.id_usuario == Usuario.id_usuario)
        .order_by(ClienteSemConta.cpf.is_(None), ClienteSemConta.id_cliente_sem_conta)
        .limit(1)
        .scalar_subquery()
    )


# mesma busca da lista sem conta: só dígitos procura o CPF exato; texto procura parte do nome ou do e-mail
def consulta_clientes(id_usuario: uuid.UUID | None = None, busca: str | None = None) -> Select:
    consulta = (
        select(
            Usuario.id_usuario, Usuario.nome, Usuario.email, Usuario.cpf, Usuario.status_conta,
            _cadastro_da_vulto().label("id_cliente_sem_conta"),
        )
        .where(Usuario.tipo_conta == "cliente")
        .order_by(Usuario.nome, Usuario.email)
    )
    if id_usuario is not None:
        consulta = consulta.where(Usuario.id_usuario == id_usuario)
    termo = (busca or "").strip()
    digitos = "".join(c for c in termo if c.isdigit())
    if termo and digitos and not any(c.isalpha() or c == "@" for c in termo):
        consulta = consulta.where(Usuario.cpf == digitos)
    elif termo:
        padrao = f"%{termo}%"
        consulta = consulta.where(or_(Usuario.nome.ilike(padrao), Usuario.email.ilike(padrao)))
    return consulta


def listar_clientes(db: Session, limit: int, offset: int, busca: str | None = None) -> tuple[list, int]:
    consulta = consulta_clientes(busca=busca)
    total = db.scalar(select(func.count()).select_from(consulta.order_by(None).subquery()))
    linhas = db.execute(consulta.limit(limit).offset(offset)).mappings().all()
    return list(linhas), total


def buscar_cliente(db: Session, id_usuario: uuid.UUID):
    return db.execute(consulta_clientes(id_usuario=id_usuario)).mappings().first()


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
