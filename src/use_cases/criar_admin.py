from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.models import ModeloAcesso, Usuario


class ErroCriarAdmin(Exception):
    pass


# cria o login no Supabase Auth e a linha de USUARIO no modelo Admin;
# se a linha não puder ser gravada, apaga o login criado (ADR 0008)
def criar_admin(db: Session, auth, nome: str, email: str, senha: str) -> Usuario:
    email = email.strip().lower()

    id_modelo_admin = db.scalar(select(ModeloAcesso.id_modelo).where(ModeloAcesso.eh_admin))
    if id_modelo_admin is None:
        raise ErroCriarAdmin("Modelo Admin não existe: rode as migrations (alembic upgrade head)")
    if db.scalar(select(Usuario.id_usuario).where(Usuario.email == email)) is not None:
        raise ErroCriarAdmin(f"Já existe usuário com o e-mail {email}")

    id_usuario = auth.criar_login(email, senha)
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
        auth.apagar_login(id_usuario)
        raise
    return usuario
