# Cria a primeira conta Admin (login no Supabase Auth + linha em USUARIO).
# Se já existir login com o e-mail, usa esse login e a senha continua a mesma.
# Uso, na raiz do repositório com o venv ativo:
#   python -m scripts.criar_admin
# Precisa de SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY e DATABASE_URL no .env.
from getpass import getpass

from src.database.session import SessionLocal
from src.repositories.usuario_repository import buscar_login
from src.use_cases.criar_admin import ErroCriarAdmin, criar_admin
from src.utils.supabase_admin import ErroSupabase, SupabaseAdmin


def main() -> None:
    nome = input("Nome: ")
    email = input("E-mail: ").strip().lower()

    with SessionLocal() as db:
        senha = None
        if buscar_login(db, email):
            print("Já existe login no Supabase com esse e-mail; ele será usado e a senha continua a mesma.")
            if input("Continuar? [s/N] ").strip().lower() != "s":
                raise SystemExit("Cancelado.")
        else:
            senha = getpass("Senha (mínimo 6 caracteres, não aparece na tela): ")
            if getpass("Repita a senha: ") != senha:
                raise SystemExit("As senhas não conferem.")

        try:
            usuario = criar_admin(db, SupabaseAdmin(), nome, email, senha)
        except (ErroCriarAdmin, ErroSupabase) as erro:
            raise SystemExit(str(erro))
    print(f"Admin criado: {usuario.email} (id {usuario.id_usuario})")


if __name__ == "__main__":
    main()
