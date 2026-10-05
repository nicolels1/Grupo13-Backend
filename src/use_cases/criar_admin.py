import uuid

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from src.models.models import ModeloAcesso, Usuario


class ErroCriarAdmin(Exception):
    pass


# id do login no Supabase Auth com esse e-mail, se já existir
def buscar_login(db: Session, email: str) -> uuid.UUID | None:
    return db.scalar(text("SELECT id FROM auth.users WHERE lower(email) = :email"), {"email": email})


def login_confirmado(db: Session, id_usuario: uuid.UUID) -> bool:
    consulta = text("SELECT email_confirmed_at IS NOT NULL FROM auth.users WHERE id = :id")
    return bool(db.scalar(consulta, {"id": id_usuario}))


# cria a linha de USUARIO no modelo Admin; usa o login que já existe com o e-mail
# ou cria um novo (exige senha). Se a linha não puder ser gravada, apaga o login
# que acabou de criar (ADR 0008); um login que já existia não é apagado.
def criar_admin(db: Session, auth, nome: str, email: str, senha: str | None = None) -> Usuario:
    email = email.strip().lower()

    id_modelo_admin = db.scalar(select(ModeloAcesso.id_modelo).where(ModeloAcesso.eh_admin))
    if id_modelo_admin is None:
        raise ErroCriarAdmin("Modelo Admin não existe: rode as migrations (alembic upgrade head)")
    if db.scalar(select(Usuario.id_usuario).where(Usuario.email == email)) is not None:
        raise ErroCriarAdmin(f"Já existe usuário com o e-mail {email}")

    id_usuario = buscar_login(db, email)
    login_novo = id_usuario is None
    if login_novo:
        if not senha:
            raise ErroCriarAdmin("Senha obrigatória para criar um login novo")
        id_usuario = auth.criar_login(email, senha)
    elif not login_confirmado(db, id_usuario):
        auth.confirmar_email(id_usuario)

    usuario = Usuario(
        id_usuario=id_usuario,
        nome=nome.strip(),
        email=email,
        tipo_conta="interna",
        status_conta="ativa",
        id_modelo_acesso=id_modelo_admin,
    )
    try:
        db.add(usuario)
        db.commit()
    except Exception:
        db.rollback()
        if login_novo:
            auth.apagar_login(id_usuario)
        raise
    return usuario
