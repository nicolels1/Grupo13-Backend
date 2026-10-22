from typing import Literal

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from src.database.session import get_db
from src.dependencies.permissoes import exige_permissao, marca_da_transferencia, marca_permitida
from src.entities.comum import LIMITE_MAXIMO, LIMITE_PADRAO, Pagina
from src.entities.transferencias import (
    TransferenciaCancelar, TransferenciaCriar, TransferenciaEnviar, TransferenciaReceber, TransferenciaSaida,
)
from src.models.contas import Usuario
from src.repositories import marca_repository
from src.use_cases import transferencias
from src.use_cases.permissoes import exigir_marca

router = APIRouter(tags=["transferencias"])

# detalhe e escrita só de registro de uma marca que a pessoa vê (ver_marca_lorenzi, ver_marca_vulto)
MARCA_DA_TRANSFERENCIA = Depends(marca_da_transferencia)

Status = Literal["solicitada", "enviada", "recebida", "cancelada"]
# quem tem alguma permissão de transferência vê a lista e os detalhes
VER_TRANSFERENCIAS = exige_permissao("solicitar_transferencia", "enviar_transferencia", "receber_transferencia")
ERROS = {
    404: {"description": "Transferência, unidade ou variante não encontrada"},
    422: {"description": "Etapa errada, estoque indisponível ou regra do CD"},
}


@router.get("/transferencias", response_model=Pagina[TransferenciaSaida])
def listar(
    status_: Status | None = Query(default=None, alias="status"),
    id_unidade: int | None = Query(default=None, description="Transferências que saem ou chegam nesta unidade"),
    marca: str | None = Depends(marca_permitida),
    limit: int = Query(LIMITE_PADRAO, ge=1, le=LIMITE_MAXIMO),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(VER_TRANSFERENCIAS),
):
    return transferencias.listar(db, status_, id_unidade, limit, offset, marca)


@router.get(
    "/transferencias/{id_transferencia}",
    dependencies=[MARCA_DA_TRANSFERENCIA],
    response_model=TransferenciaSaida,
    responses={404: ERROS[404]},
)
def buscar(id_transferencia: int, db: Session = Depends(get_db), usuario: Usuario = Depends(VER_TRANSFERENCIAS)):
    return transferencias.buscar(db, id_transferencia)


@router.post("/transferencias", status_code=status.HTTP_201_CREATED, response_model=TransferenciaSaida, responses=ERROS)
def solicitar(
    dados: TransferenciaCriar,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(exige_permissao("solicitar_transferencia")),
):
    exigir_marca(db, usuario, marca_repository.da_unidade(db, dados.id_unidade_origem))
    return transferencias.solicitar(db, usuario.id_usuario, dados.model_dump())


@router.post(
    "/transferencias/{id_transferencia}/enviar",
    dependencies=[MARCA_DA_TRANSFERENCIA],
    response_model=TransferenciaSaida,
    responses=ERROS,
)
def enviar(
    id_transferencia: int,
    dados: TransferenciaEnviar,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(exige_permissao("enviar_transferencia")),
):
    return transferencias.enviar(db, id_transferencia, usuario.id_usuario, dados.model_dump())


@router.post(
    "/transferencias/{id_transferencia}/receber",
    dependencies=[MARCA_DA_TRANSFERENCIA],
    response_model=TransferenciaSaida,
    responses=ERROS,
)
def receber(
    id_transferencia: int,
    dados: TransferenciaReceber,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(exige_permissao("receber_transferencia")),
):
    return transferencias.receber(db, id_transferencia, usuario.id_usuario, dados.model_dump())


# quem pode solicitar também pode cancelar antes do envio
@router.post(
    "/transferencias/{id_transferencia}/cancelar",
    dependencies=[MARCA_DA_TRANSFERENCIA],
    response_model=TransferenciaSaida,
    responses=ERROS,
)
def cancelar(
    id_transferencia: int,
    dados: TransferenciaCancelar,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(exige_permissao("solicitar_transferencia")),
):
    return transferencias.cancelar(db, id_transferencia, usuario.id_usuario, dados.motivo)
