from src.utils.supabase_admin import SupabaseAdmin, SupabaseLogin
from src.utils.supabase_storage import SupabaseStorage


# clientes do Supabase como dependências, para os testes trocarem por versões falsas
def get_supabase_admin() -> SupabaseAdmin:
    return SupabaseAdmin()


def get_supabase_login() -> SupabaseLogin:
    return SupabaseLogin()


def get_storage() -> SupabaseStorage:
    return SupabaseStorage()
