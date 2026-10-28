from decimal import Decimal
from typing import Literal

from pydantic import BaseModel


class PecaSaida(BaseModel):
    id_produto: int
    nome: str
    marca: str
    descricao: str
    preco_a_partir: Decimal
    cores: list[str]
    tamanhos_no_site: list[str]
    foto: str | None
    link: str


class BuscaPecasSaida(BaseModel):
    items: list[PecaSaida]
    # exato: achou o que foi pedido; parecidas ou novidades: não achou e mostra outra coisa
    resultado: Literal["exato", "parecidas", "novidades"]


# quantidade só quando está acabando (até 3); acima disso, só que tem
class FaixaEstoque(BaseModel):
    tem: bool
    quantidade: int | None
    acabando: bool


class EstoqueNaLoja(FaixaEstoque):
    id_unidade: int
    nome: str
    endereco: str


class EstoqueSaida(BaseModel):
    id_variante: int
    produto: str
    marca: str
    cor: str
    tamanho: str
    preco: Decimal
    site: FaixaEstoque
    lojas: list[EstoqueNaLoja]


class LinkCarrinhoSaida(BaseModel):
    id_variante: int
    produto: str
    cor: str
    tamanho: str
    link: str


class HorarioHoje(BaseModel):
    aberta_agora: bool
    texto: str


class HorarioDoDia(BaseModel):
    dia: str
    horario: str


class LojaSaida(BaseModel):
    id_unidade: int
    nome: str
    endereco: str
    hoje: HorarioHoje
    semana: list[HorarioDoDia]


class LojasDaMarca(BaseModel):
    marca: str
    lojas: list[LojaSaida]


class VocabularioSaida(BaseModel):
    categorias: list[str]
    cores: list[str]
