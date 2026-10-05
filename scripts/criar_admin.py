# Cria a primeira conta Admin (login no Supabase Auth + linha em USUARIO).
# Uso, na raiz do repositório com o venv ativo:
#   python -m scripts.criar_admin
# Precisa de SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY e DATABASE_URL no .env.
from getpass import getpass

from src.database.session import SessionLocal
from src.use_cases.criar_admin import ErroCriarAdmin, criar_admin
from src.utils.supabase_admin import SupabaseAdmin


def main() -> None:
    nome = input("Nome: ")
    email = input("E-mail: ")
    senha = getpass("Senha (não aparece na tela): ")
    if getpass("Repita a senha: ") != senha:
        raise SystemExit("As senhas não conferem.")

    with SessionLocal() as db:
        try:
            usuario = criar_admin(db, SupabaseAdmin(), nome, email, senha)
        except ErroCriarAdmin as erro:
            raise SystemExit(str(erro))
    print(f"Admin criado: {usuario.email} (id {usuario.id_usuario})")


if __name__ == "__main__":
    main()
