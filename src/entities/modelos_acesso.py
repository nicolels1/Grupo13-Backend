from typing import Literal

from pydantic import BaseModel, Field, field_validator

from src.entities.comum import sem_espacos

# códigos que as migrações cadastram na tabela permissao (seção 6 do case). Nas respostas, o OpenAPI
# publica a lista e o frontend recebe os códigos já tipados. Permissão nova entra na migração e aqui;
# o teste test_codigos_de_permissao confere que as duas listas são iguais.
# As entradas continuam texto: código desconhecido recebe a mensagem da regra de negócio, não um 422
CodigoPermissao = Literal[
    "gerenciar_contas",
    "gerenciar_modelos_acesso",
    "gerenciar_catalogo",
    "gerenciar_unidades",
    "movimentar_estoque",
    "definir_estoque_minimo",
    "solicitar_transferencia",
    "enviar_transferencia",
    "receber_transferencia",
    "registrar_venda_fisica",
    "registrar_troca_devolucao",
    "preparar_entregar_pedido",
    "cancelar_pedido_equipe",
    "corrigir_cadastro_cliente",
    "atender_chamado",
    "moderar_avaliacoes",
    "gerenciar_fidelidade",
]


class PermissaoSaida(BaseModel):
    codigo: CodigoPermissao
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
    permissoes: list[CodigoPermissao]
