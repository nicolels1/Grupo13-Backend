import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.entities.comum import sem_espacos

Canal = Literal["loja_fisica", "online"]


# ---------- entrada ----------

class ItemSolicitado(BaseModel):
    id_variante: int
    canal_saida: Canal
    canal_entrada: Canal
    quantidade: int = Field(gt=0)


class TransferenciaCriar(BaseModel):
    id_unidade_origem: int
    id_unidade_destino: int
    itens: list[ItemSolicitado] = Field(min_length=1)

    @model_validator(mode="after")
    def conferir(self):
        if self.id_unidade_origem == self.id_unidade_destino:
            raise ValueError("Origem e destino precisam ser diferentes")
        chaves = [(i.id_variante, i.canal_saida, i.canal_entrada) for i in self.itens]
        if len(set(chaves)) != len(chaves):
            raise ValueError("Item repetido: junte as quantidades da mesma variante e canais")
        return self


class ItemQuantidade(BaseModel):
    id_item_transferencia: int
    quantidade: int = Field(ge=0)


# sem itens: envia o que foi solicitado
class TransferenciaEnviar(BaseModel):
    itens: list[ItemQuantidade] = Field(default_factory=list)


# sem itens: recebeu tudo o que foi enviado. Se chegou menos, a diferença vira perda ou avaria
class TransferenciaReceber(BaseModel):
    itens: list[ItemQuantidade] = Field(default_factory=list)
    tipo_diferenca: Literal["perda", "avaria"] = "perda"
    motivo_diferenca: str | None = Field(default=None, max_length=500)

    _limpa = field_validator("motivo_diferenca", mode="before")(sem_espacos)


class TransferenciaCancelar(BaseModel):
    motivo: str = Field(min_length=3, max_length=500)

    _limpa = field_validator("motivo", mode="before")(sem_espacos)


# ---------- saída ----------

class ItemTransferenciaSaida(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_item_transferencia: int
    id_variante: int
    canal_saida: str
    canal_entrada: str
    quantidade_solicitada: int
    quantidade_enviada: int | None
    quantidade_recebida: int | None


class TransferenciaSaida(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_transferencia: int
    id_unidade_origem: int
    id_unidade_destino: int
    status: str
    id_solicitante: uuid.UUID
    id_enviado_por: uuid.UUID | None
    id_recebido_por: uuid.UUID | None
    id_cancelado_por: uuid.UUID | None
    motivo_cancelamento: str | None
    solicitada_em: datetime
    enviada_em: datetime | None
    recebida_em: datetime | None
    cancelada_em: datetime | None
    itens: list[ItemTransferenciaSaida]
