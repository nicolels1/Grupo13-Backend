from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from src.database.session import get_db
from src.entities.comum import Lista, campos_alterados
from src.entities.modelos_acesso import ModeloAlterar, ModeloCriar, ModeloPermissoes, ModeloSaida, PermissaoSaida
from src.middlewares.permissoes import exige_permissao
from src.models.contas import Usuario
from src.use_cases import modelos_acesso

router = APIRouter(tags=["modelos de acesso"])

# Gestão: só o Admin tem gerenciar_modelos_acesso
so_admin = exige_permissao("gerenciar_modelos_acesso")
ERROS = {
    404: {"description": "Modelo de acesso não encontrado"},
    409: {"description": "Nome já usado"},
    422: {"description": "Permissão desconhecida ou da Gestão, Admin não editável, modelo com pessoas"},
}


@router.get("/permissoes", response_model=Lista[PermissaoSaida])
def listar_permissoes(db: Session = Depends(get_db), usuario: Usuario = Depends(so_admin)):
    return {"items": modelos_acesso.listar_permissoes(db)}


@router.get("/modelos-acesso", response_model=Lista[ModeloSaida])
def listar_modelos(db: Session = Depends(get_db), usuario: Usuario = Depends(so_admin)):
    return {"items": modelos_acesso.listar_modelos(db)}


@router.get("/modelos-acesso/{id_modelo}", response_model=ModeloSaida, responses={404: ERROS[404]})
def buscar_modelo(id_modelo: int, db: Session = Depends(get_db), usuario: Usuario = Depends(so_admin)):
    return modelos_acesso.buscar_modelo(db, id_modelo)


@router.post("/modelos-acesso", status_code=status.HTTP_201_CREATED, response_model=ModeloSaida, responses=ERROS)
def criar_modelo(dados: ModeloCriar, db: Session = Depends(get_db), usuario: Usuario = Depends(so_admin)):
    return modelos_acesso.criar_modelo(db, dados.nome, dados.permissoes)


@router.patch("/modelos-acesso/{id_modelo}", response_model=ModeloSaida, responses=ERROS)
def alterar_modelo(
    id_modelo: int, dados: ModeloAlterar, db: Session = Depends(get_db), usuario: Usuario = Depends(so_admin)
):
    return modelos_acesso.alterar_modelo(db, id_modelo, campos_alterados(dados))


@router.put("/modelos-acesso/{id_modelo}/permissoes", response_model=ModeloSaida, responses=ERROS)
def trocar_permissoes(
    id_modelo: int, dados: ModeloPermissoes, db: Session = Depends(get_db), usuario: Usuario = Depends(so_admin)
):
    return modelos_acesso.trocar_permissoes(db, id_modelo, dados.permissoes)
