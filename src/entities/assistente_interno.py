from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from src.entities.comum import sem_espacos

TAMANHO_DA_PERGUNTA = 1000


class PerguntaEntrada(BaseModel):
    texto: str = Field(min_length=1, max_length=TAMANHO_DA_PERGUNTA)
    id_conversa: int | None = Field(None, description="Vazio, ou parada há 30 min: começa uma conversa nova")

    _sem_espacos = field_validator("texto", mode="before")(sem_espacos)


class Link(BaseModel):
    rotulo: str
    url: str


class RespostaAssistente(BaseModel):
    id_conversa: int = Field(description="Mande de volta na próxima pergunta para continuar a conversa")
    resposta: str
    links: list[Link]
    ferramentas: list[str] = Field(description="Ferramentas usadas, na ordem")
    numeros_conferidos: bool | None = Field(
        description="A resposta passou na conferência dos números (falso: veio o texto fixo da ferramenta); "
                    "vazio quando não houve resposta do modelo para conferir"
    )


class MensagemConversa(BaseModel):
    papel: Literal["usuario", "assistente"]
    texto: str | None = Field(description="Vazio depois de 90 dias")
    criado_em: datetime


class ConversaSaida(BaseModel):
    id_conversa: int
    iniciada_em: datetime
    avaliacao: Literal["positiva", "negativa"] | None
    mensagens: list[MensagemConversa]


class AvaliacaoEntrada(BaseModel):
    avaliacao: Literal["positiva", "negativa"]
