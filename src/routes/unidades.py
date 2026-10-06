from typing import Literal

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from src.database.session import get_db
from src.entities.comum import Lista, campos_alterados
from src.entities.unidades import UnidadeAlterar, UnidadeCriar, UnidadeSaida
from src.middlewares.permissoes import exige_permissao
from src.models.contas import Usuario
from src.repositories import unidade_repository
from src.use_cases import unidades

router = APIRouter(tags=["unidades"])

ERROS_UNIDADE = {
    404: {"description": "Unidade não encontrada"},
    409: {"description": "Unidade já existe"},
    422: {"description": "Dados inválidos ou regra do CD"},
}


# público: o cliente escolhe a loja de retirada
@router.get("/unidades", response_model=Lista[UnidadeSaida])
def listar_unidades(
    ativo: bool | None = None, tipo: Literal["loja", "cd"] | None = None, db: Session = Depends(get_db)
):
    return {"items": unidade_repository.listar_unidades(db, ativo, tipo)}


@router.get("/unidades/{id_unidade}", response_model=UnidadeSaida, responses={404: ERROS_UNIDADE[404]})
def buscar_unidade(id_unidade: int, db: Session = Depends(get_db)):
    return unidades.buscar_unidade(db, id_unidade)


# Gestão: só o Admin tem gerenciar_unidades
@router.post("/unidades", status_code=status.HTTP_201_CREATED, response_model=UnidadeSaida, responses=ERROS_UNIDADE)
def criar_unidade(
    dados: UnidadeCriar,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(exige_permissao("gerenciar_unidades")),
):
    return unidades.criar_unidade(db, dados.model_dump())


@router.patch("/unidades/{id_unidade}", response_model=UnidadeSaida, responses=ERROS_UNIDADE)
def alterar_unidade(
    id_unidade: int,
    dados: UnidadeAlterar,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(exige_permissao("gerenciar_unidades")),
):
    return unidades.alterar_unidade(db, id_unidade, campos_alterados(dados, nullaveis=("complemento",)))
