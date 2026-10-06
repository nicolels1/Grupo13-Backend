import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Canal = Literal["loja_fisica", "online"]
# tipos que a equipe registra à mão; os outros (venda, transferência, troca...) nascem dos fluxos
TipoManual = Literal["recebimento", "avaria", "perda", "ajuste"]
Granularidade = Literal["hora", "dia", "semana"]


# ---------- entrada ----------

class NovaMovimentacao(BaseModel):
    id_variante: int
    id_unidade: int
    canal: Canal
    tipo: TipoManual
    quantidade: int = Field(
        description="Recebimento, avaria e perda: número de peças (positivo). Ajuste: com sinal (+ entra, - sai)."
    )
    motivo: str | None = Field(default=None, max_length=500, description="Obrigatório em avaria, perda e ajuste")


class NovaRealocacao(BaseModel):
    id_variante: int
    id_unidade: int
    canal_origem: Canal = Field(description="As peças saem deste canal e entram no outro")
    quantidade: int = Field(gt=0)


class NovoMinimo(BaseModel):
    id_variante: int
    id_unidade: int
    canal: Canal
    estoque_minimo: int | None = Field(ge=0, description="Vazio remove o mínimo")


# ---------- saída ----------

class EstoqueItem(BaseModel):
    id_variante: int
    sku: str
    produto: str
    cor: str
    tamanho: str
    id_unidade: int
    unidade: str
    canal: Canal
    quantidade: int
    quantidade_reservada: int
    disponivel: int
    estoque_minimo: int | None
    abaixo_minimo: bool


# "ver estoque em": quanto havia naquele momento (reservas ficam de fora, ADR 0006)
class EstoqueHistoricoItem(BaseModel):
    id_variante: int
    sku: str
    produto: str
    cor: str
    tamanho: str
    id_unidade: int
    unidade: str
    canal: Canal
    quantidade: int


class MovimentacaoSaida(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_movimentacao: int
    id_variante: int
    id_unidade: int
    canal: Canal
    tipo: str
    quantidade: int
    motivo: str | None
    id_usuario: uuid.UUID | None
    id_pedido: int | None
    id_transferencia: int | None
    id_chamado: int | None
    criado_em: datetime


class MovimentacaoItem(MovimentacaoSaida):
    sku: str
    produto: str
    unidade: str
    autor: str | None


class PontoEvolucao(BaseModel):
    inicio_periodo: datetime
    loja_fisica: int
    online: int


class Evolucao(BaseModel):
    id_variante: int
    id_unidade: int | None = Field(description="Vazio: soma de todas as unidades")
    granularidade: Granularidade
    pontos: list[PontoEvolucao]


# conferência do ADR 0005: deve vir sempre vazia
class Divergencia(BaseModel):
    id_variante: int
    id_unidade: int
    canal: Canal
    quantidade: int
    soma_movimentacoes: int
