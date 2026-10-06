import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from src.entities.comum import sem_espacos
from src.entities.contas import PADRAO_EMAIL, ModeloAcessoSaida

TipoConta = Literal["interna", "cliente"]
StatusConta = Literal["pendente_ativacao", "ativa", "inativa"]
Efeito = Literal["acrescentar", "retirar"]


# ---------- entrada ----------

# conta interna criada pelo Admin (ADR 0008). Sem senha provisória, o Supabase manda um
# convite por e-mail; com ela, a conta já entra e a pessoa troca a senha depois
class ContaInternaCriar(BaseModel):
    nome: str = Field(min_length=2, max_length=150)
    email: str = Field(max_length=255, pattern=PADRAO_EMAIL, description="E-mail corporativo")
    id_modelo_acesso: int
    id_unidade: int | None = Field(default=None, description="Informativa: só define o filtro inicial das telas")
    senha_provisoria: str | None = Field(
        default=None, min_length=6, max_length=72,
        description="Opcional. Vazia: convite por e-mail (exige servidor de e-mail configurado no Supabase)",
    )

    _limpa = field_validator("nome", "email", mode="before")(sem_espacos)

    @field_validator("email")
    @classmethod
    def email_em_minusculas(cls, valor: str) -> str:
        return valor.lower()


# modelo e unidade só valem para conta interna; status ativa/inativa vale para qualquer conta
class UsuarioAlterar(BaseModel):
    nome: str | None = Field(default=None, min_length=2, max_length=150)
    id_modelo_acesso: int | None = None
    id_unidade: int | None = Field(default=None, description="null tira a unidade")
    status_conta: Literal["ativa", "inativa"] | None = Field(
        default=None, description="inativa bloqueia o login no Supabase; ativa desbloqueia"
    )

    _limpa = field_validator("nome", mode="before")(sem_espacos)


class ExcecaoDefinir(BaseModel):
    efeito: Efeito


# ---------- saída ----------

class UsuarioItem(BaseModel):
    id_usuario: uuid.UUID
    nome: str
    email: str
    tipo_conta: TipoConta
    status_conta: StatusConta
    id_modelo_acesso: int | None
    modelo_acesso: str | None
    id_unidade: int | None
    unidade: str | None
    criado_em: datetime


class ExcecaoSaida(BaseModel):
    codigo: str
    efeito: Efeito


class UsuarioDetalhe(BaseModel):
    id_usuario: uuid.UUID
    nome: str
    email: str
    tipo_conta: TipoConta
    status_conta: StatusConta
    id_unidade: int | None
    modelo_acesso: ModeloAcessoSaida | None
    excecoes: list[ExcecaoSaida]
    permissoes: list[str] = Field(description="Permissões efetivas: modelo + acrescentadas - retiradas")
    convite_pendente: bool = Field(description="O login ainda não foi confirmado (convite não aceito)")
