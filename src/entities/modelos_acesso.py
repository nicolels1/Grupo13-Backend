from pydantic import BaseModel, Field, field_validator

from src.entities.comum import sem_espacos


class PermissaoSaida(BaseModel):
    codigo: str
    descricao: str
    so_admin: bool


class ModeloCriar(BaseModel):
    nome: str = Field(min_length=2, max_length=100)
    permissoes: list[str] = Field(default_factory=list, description="Códigos das permissões")

    _limpa = field_validator("nome", mode="before")(sem_espacos)


# desativar = ativo false; modelo com pessoas ligadas não é desativado (o banco confere)
class ModeloAlterar(BaseModel):
    nome: str | None = Field(default=None, min_length=2, max_length=100)
    ativo: bool | None = None

    _limpa = field_validator("nome", mode="before")(sem_espacos)


class ModeloPermissoes(BaseModel):
    permissoes: list[str] = Field(description="Lista completa: substitui as permissões atuais do modelo")


class ModeloSaida(BaseModel):
    id_modelo: int
    nome: str
    eh_admin: bool
    ativo: bool
    pessoas: int
    # o Admin tem todas as permissões, inclusive as futuras
    permissoes: list[str]
