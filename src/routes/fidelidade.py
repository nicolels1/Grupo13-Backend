from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from src.database.session import get_db
from src.dependencies.permissoes import exige_permissao
from src.entities.comum import Lista, Marca
from src.entities.fidelidade import RegraCriar, RegraSaida
from src.models.contas import Usuario
from src.use_cases import fidelidade

router = APIRouter(prefix="/fidelidade", tags=["fidelidade"])

GERENCIAR = exige_permissao("gerenciar_fidelidade")


@router.get(
    "/regras", response_model=Lista[RegraSaida],
    description="Regras de cada marca, da mais nova para a mais antiga; `vigente` marca a que vale hoje.",
)
def listar_regras(
    marca: Marca | None = Query(None), db: Session = Depends(get_db), usuario: Usuario = Depends(GERENCIAR),
):
    return {"items": fidelidade.listar_regras(db, marca)}


@router.post(
    "/regras", status_code=status.HTTP_201_CREATED, response_model=RegraSaida,
    description="Cria a regra de uma marca a partir de uma data futura. Vale dali em diante e nunca muda um "
                "ponto já ganho; uma regra que ainda não começou se corrige com outra para a mesma data.",
)
def criar_regra(dados: RegraCriar, db: Session = Depends(get_db), usuario: Usuario = Depends(GERENCIAR)):
    return fidelidade.criar_regra(db, usuario.id_usuario, dados.model_dump())
