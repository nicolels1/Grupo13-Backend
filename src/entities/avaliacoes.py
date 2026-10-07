import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from src.entities.comum import sem_espacos

StatusAvaliacao = Literal["publicada", "oculta"]
StatusDenuncia = Literal["pendente", "procedente", "improcedente"]


# ---------- entrada ----------

class AvaliacaoCriar(BaseModel):
    id_item_pedido: int = Field(description="Item de um pedido entregue da própria conta")
    nota: int = Field(ge=1, le=5)
    texto: str | None = Field(default=None, max_length=2000)

    _limpa = field_validator("texto", mode="before")(sem_espacos)


# o cliente edita por 7 dias e não apaga (case, seção 5)
class AvaliacaoAlterar(BaseModel):
    nota: int | None = Field(default=None, ge=1, le=5)
    texto: str | None = Field(default=None, max_length=2000)

    _limpa = field_validator("texto", mode="before")(sem_espacos)


class DenunciaCriar(BaseModel):
    motivo: str = Field(min_length=3, max_length=500)

    _limpa = field_validator("motivo", mode="before")(sem_espacos)


class Ocultacao(BaseModel):
    motivo: str = Field(min_length=3, max_length=500)

    _limpa = field_validator("motivo", mode="before")(sem_espacos)


# procedente oculta a avaliação (com o motivo da denúncia, se outro não for informado)
class AnaliseDenuncia(BaseModel):
    procedente: bool
    motivo_ocultacao: str | None = Field(default=None, max_length=500)

    _limpa = field_validator("motivo_ocultacao", mode="before")(sem_espacos)


# ---------- saída ----------

class FotoSaida(BaseModel):
    id_foto: int
    ordem: int
    url: str | None = Field(description="Link temporário; vazio se o Storage não respondeu")


class AvaliacaoSaida(BaseModel):
    id_avaliacao: int
    id_item_pedido: int
    id_produto: int
    produto: str
    cor: str
    tamanho: str
    nota: int
    texto: str | None
    status: StatusAvaliacao
    autor: str = Field(description="Só o primeiro nome de quem avaliou")
    criada_em: datetime
    editada_em: datetime | None
    votos_util: int
    fotos: list[FotoSaida] = Field(description="Saem do ar quando a avaliação é ocultada")
    # só para a moderação
    motivo_ocultacao: str | None = None
    ocultada_em: datetime | None = None
    denuncias_pendentes: int | None = None


class AvaliacoesDoProduto(BaseModel):
    items: list[AvaliacaoSaida]
    total: int
    limit: int
    offset: int
    media: Decimal | None = Field(description="Média das notas publicadas, com uma casa decimal")


class DenunciaSaida(BaseModel):
    id_denuncia: int
    id_avaliacao: int
    motivo: str
    status: StatusDenuncia
    id_cliente: uuid.UUID
    cliente: str
    id_analisada_por: uuid.UUID | None
    analisada_em: datetime | None
    criada_em: datetime
    nota: int
    texto: str | None
    status_avaliacao: StatusAvaliacao
