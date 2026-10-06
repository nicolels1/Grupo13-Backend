from sqlalchemy.orm import Session

from src.models.contas import Usuario
from src.repositories import usuario_repository as repo
from src.use_cases.erros import Conflito, RegraDeNegocio


# cria a linha de USUARIO no modelo Admin; usa o login que já existe com o e-mail
# ou cria um novo (exige senha). Se a linha não puder ser gravada, apaga o login
# que acabou de criar (ADR 0008); um login que já existia não é apagado.
def criar_admin(db: Session, auth, nome: str, email: str, senha: str | None = None) -> Usuario:
    email = email.strip().lower()

    id_modelo_admin = repo.id_modelo_admin(db)
    if id_modelo_admin is None:
        # banco sem as migrations: problema de configuração, não de quem pediu
        raise RuntimeError("Modelo Admin não existe: rode as migrations (alembic upgrade head)")
    if repo.email_em_uso(db, email):
        raise Conflito("E-mail já cadastrado")

    id_usuario = repo.buscar_login(db, email)
    login_novo = id_usuario is None
    if login_novo:
        if not senha:
            raise RegraDeNegocio("Senha obrigatória para criar um login novo")
        id_usuario = auth.criar_login(email, senha)
    elif not repo.login_confirmado(db, id_usuario):
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
