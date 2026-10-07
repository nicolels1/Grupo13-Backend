from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from src.database.session import get_db
from src.entities.comum import Lista, campos_alterados
from src.entities.enderecos import EnderecoAlterar, EnderecoCriar, EnderecoSaida
from src.middlewares.permissoes import exige_cliente
from src.models.contas import Usuario
from src.use_cases import enderecos

router = APIRouter(tags=["endereços"])

ERROS = {401: {"description": "Sem login válido"}, 403: {"description": "Só para contas de cliente"},
         404: {"description": "Endereço não encontrado"}}


# ---------- plataforma do cliente: endereços salvos da própria conta ----------

@router.get("/enderecos", response_model=Lista[EnderecoSaida], responses=ERROS)
def listar(cliente: Usuario = Depends(exige_cliente), db: Session = Depends(get_db)):
    return {"items": enderecos.listar(db, cliente)}


@router.post("/enderecos", status_code=status.HTTP_201_CREATED, response_model=EnderecoSaida, responses=ERROS)
def criar(dados: EnderecoCriar, cliente: Usuario = Depends(exige_cliente), db: Session = Depends(get_db)):
    return enderecos.criar(db, cliente, dados.model_dump())


@router.patch("/enderecos/{id_endereco}", response_model=EnderecoSaida, responses=ERROS)
def alterar(
    id_endereco: int, dados: EnderecoAlterar, cliente: Usuario = Depends(exige_cliente),
    db: Session = Depends(get_db),
):
    return enderecos.alterar(db, cliente, id_endereco, campos_alterados(dados, nullaveis=("complemento",)))


@router.delete("/enderecos/{id_endereco}", status_code=status.HTTP_204_NO_CONTENT, responses=ERROS)
def apagar(id_endereco: int, cliente: Usuario = Depends(exige_cliente), db: Session = Depends(get_db)):
    enderecos.apagar(db, cliente, id_endereco)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
