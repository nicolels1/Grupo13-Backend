import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.utils.cpf import cpf_valido, normalizar_cpf

PADRAO_EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


def _validar_cpf(valor: str) -> str:
    cpf = normalizar_cpf(valor)
    if not cpf_valido(cpf):
        raise ValueError("CPF inválido")
    return cpf


# ---------- entrada ----------

class CadastroCliente(BaseModel):
    nome: str = Field(min_length=2, max_length=150)
    email: str = Field(max_length=255, pattern=PADRAO_EMAIL)
    cpf: str = Field(description="Com ou sem máscara: 123.456.789-09 ou 12345678909")
    # mínimo do Supabase Auth; 72 é o limite do bcrypt usado por ele
    senha: str = Field(min_length=6, max_length=72)

    # roda antes do tamanho e do padrão; a senha fica como foi digitada
    @field_validator("nome", "email", mode="before")
    @classmethod
    def tirar_espacos_das_pontas(cls, valor):
        return valor.strip() if isinstance(valor, str) else valor

    @field_validator("email")
    @classmethod
    def email_em_minusculas(cls, valor: str) -> str:
        return valor.lower()

    @field_validator("cpf")
    @classmethod
    def validar_cpf(cls, valor: str) -> str:
        return _validar_cpf(valor)


class LoginCpf(BaseModel):
    cpf: str
    senha: str = Field(min_length=1, max_length=72)

    @field_validator("cpf")
    @classmethod
    def validar_cpf(cls, valor: str) -> str:
        return _validar_cpf(valor)


# ---------- saída ----------

class UsuarioSaida(BaseModel):
    # permite montar a resposta direto do model do SQLAlchemy
    model_config = ConfigDict(from_attributes=True)

    id_usuario: uuid.UUID
    nome: str
    email: str
    tipo_conta: str
    status_conta: str


class ModeloAcessoSaida(BaseModel):
    id_modelo: int
    nome: str
    eh_admin: bool


# dados da conta logada: o frontend decide a plataforma pelo tipo de conta
# e mostra só as áreas das permissões
class Perfil(UsuarioSaida):
    id_unidade: int | None
    modelo_acesso: ModeloAcessoSaida | None
    permissoes: list[str]


# sessão do Supabase devolvida no login por CPF; o frontend a entrega ao
# cliente do Supabase (supabase.auth.setSession) e segue como no login por e-mail
class Sessao(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str
