import re
from datetime import date, time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.entities.comum import Marca, sem_espacos

TipoUnidade = Literal["loja", "cd"]


def _cep(valor: str) -> str:
    cep = re.sub(r"\D", "", valor)
    if len(cep) != 8:
        raise ValueError("CEP precisa ter 8 dígitos")
    return cep


# ---------- unidade ----------

class UnidadeCriar(BaseModel):
    nome: str = Field(min_length=2, max_length=100)
    tipo: TipoUnidade
    marca: Marca = "lorenzi"
    despacha_online: bool = False
    rua: str = Field(min_length=1, max_length=200)
    numero: str = Field(min_length=1, max_length=20)
    complemento: str | None = Field(default=None, max_length=100)
    bairro: str = Field(min_length=1, max_length=100)
    cidade: str = Field(min_length=1, max_length=100)
    uf: str = Field(pattern=r"^[A-Za-z]{2}$")
    cep: str = Field(description="Com ou sem traço: 01000-000 ou 01000000")

    _limpa = field_validator("nome", "rua", "numero", "complemento", "bairro", "cidade", "uf", mode="before")(sem_espacos)

    @field_validator("uf")
    @classmethod
    def uf_maiuscula(cls, valor: str) -> str:
        return valor.upper()

    @field_validator("cep")
    @classmethod
    def validar_cep(cls, valor: str) -> str:
        return _cep(valor)

    # o CD sempre despacha online (regra do diagrama)
    @model_validator(mode="after")
    def cd_despacha(self):
        if self.tipo == "cd":
            self.despacha_online = True
        return self


# tipo não muda depois de criada: loja e CD têm estoques e regras diferentes
class UnidadeAlterar(BaseModel):
    nome: str | None = Field(default=None, min_length=2, max_length=100)
    marca: Marca | None = Field(default=None, description="Só antes da primeira movimentação ou venda")
    despacha_online: bool | None = None
    rua: str | None = Field(default=None, min_length=1, max_length=200)
    numero: str | None = Field(default=None, min_length=1, max_length=20)
    complemento: str | None = Field(default=None, max_length=100)
    bairro: str | None = Field(default=None, min_length=1, max_length=100)
    cidade: str | None = Field(default=None, min_length=1, max_length=100)
    uf: str | None = Field(default=None, pattern=r"^[A-Za-z]{2}$")
    cep: str | None = None
    ativo: bool | None = None

    _limpa = field_validator("nome", "rua", "numero", "complemento", "bairro", "cidade", "uf", mode="before")(sem_espacos)

    @field_validator("uf")
    @classmethod
    def uf_maiuscula(cls, valor: str | None) -> str | None:
        return valor.upper() if valor else valor

    @field_validator("cep")
    @classmethod
    def validar_cep(cls, valor: str | None) -> str | None:
        return _cep(valor) if valor is not None else valor


class UnidadeSaida(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_unidade: int
    nome: str
    tipo: str
    marca: Marca
    despacha_online: bool
    rua: str
    numero: str
    complemento: str | None
    bairro: str
    cidade: str
    uf: str
    cep: str
    ativo: bool


# ---------- horário de atendimento (só loja) ----------

class DiaDeAtendimento(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    dia_semana: int = Field(ge=0, le=6, description="0 = segunda ... 6 = domingo")
    abre: time
    fecha: time

    @model_validator(mode="after")
    def abre_antes(self):
        if self.abre >= self.fecha:
            raise ValueError("O horário de abrir precisa ser antes do de fechar")
        return self


# a semana inteira: o dia que não vier fica fechado
class HorarioSemana(BaseModel):
    semana: list[DiaDeAtendimento] = Field(max_length=7)

    @field_validator("semana")
    @classmethod
    def um_por_dia(cls, dias: list[DiaDeAtendimento]) -> list[DiaDeAtendimento]:
        if len({d.dia_semana for d in dias}) != len(dias):
            raise ValueError("Cada dia da semana aparece uma vez só")
        return sorted(dias, key=lambda d: d.dia_semana)


# feriado ou horário especial de uma data; sem abre e fecha, a loja fica fechada nesse dia
class ExcecaoHorarioSalvar(BaseModel):
    abre: time | None = None
    fecha: time | None = None
    motivo: str = Field(min_length=2, max_length=100, description="Ex.: Natal, horário do shopping, reforma")

    _limpa = field_validator("motivo", mode="before")(sem_espacos)

    @model_validator(mode="after")
    def horario_completo(self):
        if (self.abre is None) != (self.fecha is None):
            raise ValueError("Informe abre e fecha juntos, ou nenhum dos dois para fechada")
        if self.abre is not None and self.abre >= self.fecha:
            raise ValueError("O horário de abrir precisa ser antes do de fechar")
        return self


class ExcecaoHorarioSaida(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    data: date
    abre: time | None
    fecha: time | None
    motivo: str


class HorarioSaida(BaseModel):
    semana: list[DiaDeAtendimento]
    excecoes: list[ExcecaoHorarioSaida]
