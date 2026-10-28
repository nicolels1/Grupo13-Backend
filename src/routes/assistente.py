from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.database.session import get_db
from src.dependencies.assistente import exige_chave_assistente
from src.entities.assistente import (
    BuscaPecasSaida, EstoqueSaida, LinkCarrinhoSaida, LojasDaMarca, VocabularioSaida,
)
from src.entities.comum import Lista, Marca
from src.use_cases import assistente
from src.use_cases.horarios import BRASILIA

# ferramentas do assistente do cliente, chamadas pelo n8n com a chave no cabeçalho X-Assistente-Chave.
# Sem login de cliente: só o que a vitrine mostra a qualquer pessoa
router = APIRouter(
    prefix="/assistente",
    tags=["assistente"],
    dependencies=[Depends(exige_chave_assistente)],
    responses={401: {"description": "Chave do assistente inválida"}, 503: {"description": "Assistente indisponível"}},
)


@router.get(
    "/pecas",
    response_model=BuscaPecasSaida,
    description="Até 5 peças das duas marcas para uma descrição do cliente (ex.: camisa de botão). resultado "
                "diz se achou o que foi pedido (exato) ou se mostra peças parecidas ou novidades.",
)
def buscar_pecas(
    descricao: str = Query(min_length=2, max_length=100, description="O que o cliente procura, em palavras do catálogo"),
    cor: str | None = Query(default=None, max_length=50),
    tamanho: str | None = Query(default=None, max_length=20, description="Só peças com esse tamanho à venda"),
    marca: Marca | None = Query(default=None, description="Só quando o cliente falar a marca"),
    categoria: str | None = Query(default=None, max_length=100),
    db: Session = Depends(get_db),
):
    return assistente.buscar_pecas(db, descricao, cor, tamanho, marca, categoria)


@router.get(
    "/estoque",
    response_model=EstoqueSaida,
    responses={404: {"description": "Peça não encontrada ou sem essa cor e tamanho (a mensagem traz as opções)"}},
    description="Se a peça tem no site e em quais lojas, já sem o reservado. A quantidade só vem quando são "
                "até 3 peças (está acabando).",
)
def ver_estoque(
    id_produto: int,
    cor: str = Query(max_length=50),
    tamanho: str = Query(max_length=20),
    db: Session = Depends(get_db),
):
    return assistente.ver_estoque(db, id_produto, cor, tamanho)


@router.get(
    "/link-carrinho",
    response_model=LinkCarrinhoSaida,
    responses={
        404: {"description": "Peça não encontrada"},
        422: {"description": "Sem estoque para o site (a mensagem diz em quais lojas tem)"},
    },
    description="Link que abre o site com a peça no carrinho. Só sai com estoque para o site.",
)
def link_carrinho(id_variante: int, db: Session = Depends(get_db)):
    return assistente.link_carrinho(db, id_variante)


@router.get(
    "/lojas",
    response_model=Lista[LojasDaMarca],
    description="Lojas por marca, com endereço, o horário de hoje em texto pronto e o da semana.",
)
def lojas(db: Session = Depends(get_db)):
    return assistente.lojas(db, datetime.now(BRASILIA))


@router.get(
    "/vocabulario",
    response_model=VocabularioSaida,
    description="Categorias e cores que existem no catálogo, para buscar peças com as palavras certas.",
)
def vocabulario(db: Session = Depends(get_db)):
    return assistente.vocabulario(db)
