import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from src.entities.comum import sem_espacos

Categoria = Literal["entrega", "troca_devolucao", "estorno", "duvida", "outros"]
Status = Literal["aberto", "em_andamento", "concluido"]
Prioridade = Literal["baixa", "media", "alta"]
MotivoConclusao = Literal["resolvido", "desistencia", "sem_resposta"]


# ---------- entrada ----------

# o chamado pode apontar para um pedido do próprio cliente, um item dele, uma variante
# (dúvida sem compra), uma unidade ou um chamado anterior (case, seção 5)
class ChamadoCriar(BaseModel):
    categoria: Categoria
    assunto: str = Field(min_length=3, max_length=200)
    descricao: str = Field(min_length=1, max_length=5000)
    id_pedido: int | None = None
    id_item_pedido: int | None = Field(default=None, description="Exige id_pedido: o item precisa ser desse pedido")
    id_variante: int | None = None
    id_unidade: int | None = None
    id_chamado_anterior: int | None = None

    _limpa = field_validator("assunto", "descricao", mode="before")(sem_espacos)


class MensagemClienteCriar(BaseModel):
    conteudo: str = Field(min_length=1, max_length=5000)

    _limpa = field_validator("conteudo", mode="before")(sem_espacos)


class MensagemEquipeCriar(MensagemClienteCriar):
    interna: bool = Field(default=False, description="Visível só para a equipe, nunca para o cliente")


# só o responsável muda: prioridade e repasse para outra pessoa da equipe
class ChamadoAlterar(BaseModel):
    prioridade: Prioridade | None = None
    id_responsavel: uuid.UUID | None = Field(default=None, description="Repassa o chamado para outra pessoa")


class ChamadoConcluir(BaseModel):
    motivo: MotivoConclusao


# ---------- saída ----------

class ChamadoSaida(BaseModel):
    id_chamado: int
    categoria: Categoria
    assunto: str
    descricao: str
    status: Status
    prioridade: Prioridade | None
    motivo_encerramento: MotivoConclusao | None
    id_cliente: uuid.UUID
    cliente: str
    id_responsavel: uuid.UUID | None
    responsavel: str | None
    id_pedido: int | None
    id_item_pedido: int | None
    id_variante: int | None
    id_unidade: int | None
    id_chamado_anterior: int | None
    criado_em: datetime
    atualizado_em: datetime
    assumido_em: datetime | None
    concluido_em: datetime | None
    mensagens_nao_lidas: int = Field(description="Mensagens do outro lado (cliente ou equipe) ainda não lidas")


class MensagemSaida(BaseModel):
    id_mensagem: int
    id_chamado: int
    id_autor: uuid.UUID
    autor: str
    da_equipe: bool
    conteudo: str | None
    interna: bool
    anexo_nome: str | None
    anexo_tamanho: int | None
    lida_em: datetime | None
    criado_em: datetime


class HistoricoSaida(BaseModel):
    id_historico: int
    campo_alterado: str
    valor_anterior: str | None
    valor_novo: str
    id_autor: uuid.UUID
    autor: str
    criado_em: datetime
