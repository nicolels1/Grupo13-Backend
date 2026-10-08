import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.entities.comum import Pagina, sem_espacos


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
    imagem_url: str | None = Field(default=None, description="Foto do carrossel da página inicial; vazio sem foto")


# ---------- produto e variante ----------

Preco = Annotated[Decimal, Field(ge=0, max_digits=10, decimal_places=2)]


# carga inicial de estoque da variante numa unidade e canal (vira movimentação saldo_inicial)
class EstoqueInicial(BaseModel):
    id_unidade: int
    canal: Literal["loja_fisica", "online"]
    quantidade: int = Field(gt=0)


class VarianteCriar(BaseModel):
    sku: str = Field(min_length=1, max_length=50)
    # peça sem cor ou tamanho usa "Única" e "U" (glossário)
    cor: str = Field(default="Única", min_length=1, max_length=50)
    tamanho: str = Field(default="U", min_length=1, max_length=20)
    preco: Preco
    estoque_inicial: list[EstoqueInicial] = Field(default_factory=list)

    @model_validator(mode="after")
    def um_saldo_por_local(self):
        locais = [(e.id_unidade, e.canal) for e in self.estoque_inicial]
        if len(set(locais)) != len(locais):
            raise ValueError("Estoque inicial repetido para a mesma unidade e canal")
        return self

    _limpa = field_validator("sku", "cor", "tamanho", mode="before")(sem_espacos)

    @field_validator("sku")
    @classmethod
    def sku_maiusculo(cls, valor: str) -> str:
        return valor.upper()


# cor e tamanho não mudam: o estoque e os pedidos estão presos à variante
class VarianteAlterar(BaseModel):
    sku: str | None = Field(default=None, min_length=1, max_length=50)
    preco: Preco | None = None
    ativo: bool | None = None

    _limpa = field_validator("sku", mode="before")(sem_espacos)

    @field_validator("sku")
    @classmethod
    def sku_maiusculo(cls, valor: str | None) -> str | None:
        return valor.upper() if valor else valor


class VarianteSaida(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_variante: int
    id_produto: int
    sku: str
    cor: str
    tamanho: str
    preco: Decimal
    ativo: bool
    disponivel: bool | None = Field(
        default=None,
        description="Tem peça para vender online (sem a quantidade). Vem na lista e no detalhe do produto",
    )


# ordem da lista de produtos; novidades = cadastrados por último primeiro;
# relevancia = mais parecidos com a busca primeiro (padrão quando há busca)
OrdemProdutos = Literal["relevancia", "nome", "novidades", "menor_preco", "maior_preco"]


class ProdutoCriar(BaseModel):
    id_categoria: int
    nome: str = Field(min_length=2, max_length=150)
    descricao_tecnica: str = Field(min_length=1)
    descricao_cliente: str = Field(min_length=1)
    variantes: list[VarianteCriar] = Field(default_factory=list)

    _limpa = field_validator("nome", "descricao_tecnica", "descricao_cliente", mode="before")(sem_espacos)


class ProdutoAlterar(BaseModel):
    id_categoria: int | None = None
    nome: str | None = Field(default=None, min_length=2, max_length=150)
    descricao_tecnica: str | None = Field(default=None, min_length=1)
    descricao_cliente: str | None = Field(default=None, min_length=1)
    ativo: bool | None = None

    _limpa = field_validator("nome", "descricao_tecnica", "descricao_cliente", mode="before")(sem_espacos)


class ImagemSaida(BaseModel):
    id_imagem: int
    id_produto: int
    cor: str | None = Field(description="Vazia: vale para todas as cores")
    ordem: int
    url: str


# a foto em si não muda: para trocar, envie outra e mude a ordem
class ImagemAlterar(BaseModel):
    cor: str | None = Field(default=None, max_length=50, description="null: passa a valer para todas as cores")
    ordem: int | None = Field(default=None, ge=1)

    _limpa = field_validator("cor", mode="before")(sem_espacos)


class ProdutoSaida(BaseModel):
    id_produto: int
    id_categoria: int
    nome: str
    descricao_tecnica: str | None = Field(description="Só para quem gerencia o catálogo; vazia na visão pública")
    descricao_cliente: str
    ativo: bool
    variantes: list[VarianteSaida]
    imagens: list[ImagemSaida] = Field(default_factory=list, description="Na ordem de exibição")


class PaginaProdutos(Pagina[ProdutoSaida]):
    busca_alternativa: Literal["parecidas", "novidades"] | None = Field(
        default=None,
        description="Só na vitrine, quando a busca não achou nada: 'parecidas' traz as peças mais próximas "
                    "do termo; 'novidades', as mais recentes. Vazio quando achou o que foi buscado",
    )


class HistoricoPrecoSaida(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_historico_preco: int
    id_variante: int
    preco_anterior: Decimal | None
    preco_novo: Decimal
    id_alterado_por: uuid.UUID | None
    alterado_em: datetime
