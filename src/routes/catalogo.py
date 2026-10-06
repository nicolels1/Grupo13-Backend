from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from src.database.session import get_db
from src.entities.catalogo import CategoriaAlterar, CategoriaCriar, CategoriaSaida
from src.entities.comum import Lista, campos_alterados
from src.middlewares.permissoes import exige_permissao
from src.models.contas import Usuario
from src.repositories import catalogo_repository
from src.use_cases import catalogo

router = APIRouter(tags=["catalogo"])

ERROS_CATEGORIA = {404: {"description": "Categoria não encontrada"}, 409: {"description": "Categoria já existe"}}


# público: a vitrine do cliente também lista as categorias
@router.get("/categorias", response_model=Lista[CategoriaSaida])
def listar_categorias(ativo: bool | None = None, db: Session = Depends(get_db)):
    return {"items": catalogo_repository.listar_categorias(db, ativo)}


@router.post(
    "/categorias", status_code=status.HTTP_201_CREATED, response_model=CategoriaSaida, responses=ERROS_CATEGORIA
)
def criar_categoria(
    dados: CategoriaCriar,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(exige_permissao("gerenciar_catalogo")),
):
    return catalogo.criar_categoria(db, dados.nome)


@router.patch("/categorias/{id_categoria}", response_model=CategoriaSaida, responses=ERROS_CATEGORIA)
def alterar_categoria(
    id_categoria: int,
    dados: CategoriaAlterar,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(exige_permissao("gerenciar_catalogo")),
):
    return catalogo.alterar_categoria(db, id_categoria, campos_alterados(dados))
