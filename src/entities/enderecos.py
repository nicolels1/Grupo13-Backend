from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.entities.comum import sem_espacos
from src.entities.unidades import _cep

CAMPOS_DE_TEXTO = ("rua", "numero", "complemento", "bairro", "cidade", "uf")


class EnderecoCriar(BaseModel):
    rua: str = Field(min_length=1, max_length=200)
    numero: str = Field(min_length=1, max_length=20)
    complemento: str | None = Field(default=None, max_length=100)
    bairro: str = Field(min_length=1, max_length=100)
    cidade: str = Field(min_length=1, max_length=100)
    uf: str = Field(pattern=r"^[A-Za-z]{2}$")
    cep: str = Field(description="Com ou sem traço: 01000-000 ou 01000000")

    _limpa = field_validator(*CAMPOS_DE_TEXTO, mode="before")(sem_espacos)

    @field_validator("uf")
    @classmethod
    def uf_maiuscula(cls, valor: str) -> str:
        return valor.upper()

    @field_validator("cep")
    @classmethod
    def validar_cep(cls, valor: str) -> str:
        return _cep(valor)


class EnderecoAlterar(BaseModel):
    rua: str | None = Field(default=None, min_length=1, max_length=200)
    numero: str | None = Field(default=None, min_length=1, max_length=20)
    complemento: str | None = Field(default=None, max_length=100)
    bairro: str | None = Field(default=None, min_length=1, max_length=100)
    cidade: str | None = Field(default=None, min_length=1, max_length=100)
    uf: str | None = Field(default=None, pattern=r"^[A-Za-z]{2}$")
    cep: str | None = None

    _limpa = field_validator(*CAMPOS_DE_TEXTO, mode="before")(sem_espacos)

    @field_validator("uf")
    @classmethod
    def uf_maiuscula(cls, valor: str | None) -> str | None:
        return valor.upper() if valor else valor

    @field_validator("cep")
    @classmethod
    def validar_cep(cls, valor: str | None) -> str | None:
        return _cep(valor) if valor is not None else valor


class EnderecoSaida(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_endereco: int
    rua: str
    numero: str
    complemento: str | None
    bairro: str
    cidade: str
    uf: str
    cep: str
