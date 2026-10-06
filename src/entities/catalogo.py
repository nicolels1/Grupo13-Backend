from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.entities.comum import sem_espacos


# ---------- categoria ----------

class CategoriaCriar(BaseModel):
    nome: str = Field(min_length=2, max_length=100)

    _limpa = field_validator("nome", mode="before")(sem_espacos)


# desativar = mandar ativo false; categoria não é apagada
class CategoriaAlterar(BaseModel):
    nome: str | None = Field(default=None, min_length=2, max_length=100)
    ativo: bool | None = None

    _limpa = field_validator("nome", mode="before")(sem_espacos)


class CategoriaSaida(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_categoria: int
    nome: str
    ativo: bool
