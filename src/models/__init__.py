# importa os models para registrá-los no Base.metadata (o Alembic lê daqui)
from src.models.models import (  # noqa: F401
    ModeloAcesso,
    ModeloPermissao,
    Permissao,
    Usuario,
    UsuarioPermissaoExcecao,
    auth_users,
)
