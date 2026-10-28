from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from src.database.session import get_db
from src.dependencies.permissoes import exige_permissao
from src.entities.comum import Lista, campos_alterados
from src.entities.unidades import (
    ExcecaoHorarioSalvar, HorarioSaida, HorarioSemana, UnidadeAlterar, UnidadeCriar, UnidadeSaida,
)
from src.models.contas import Usuario
from src.use_cases import horarios, unidades
from src.use_cases.horarios import BRASILIA

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
    return {"items": unidades.listar_unidades(db, ativo, tipo)}


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


# ---------- horário de atendimento (só loja) ----------

ERROS_HORARIO = {
    404: {"description": "Unidade não encontrada"},
    422: {"description": "Dados inválidos, unidade que não é loja ou data que já passou"},
}
GERENCIAR_UNIDADES = Depends(exige_permissao("gerenciar_unidades"))


def _hoje() -> date:
    return datetime.now(BRASILIA).date()


# público, como a lista de unidades: o horário da loja não é segredo
@router.get("/unidades/{id_unidade}/horario", response_model=HorarioSaida, responses=ERROS_HORARIO)
def ver_horario(id_unidade: int, db: Session = Depends(get_db)):
    return horarios.ver_horario(db, id_unidade, _hoje())


@router.put(
    "/unidades/{id_unidade}/horario",
    response_model=HorarioSaida,
    responses=ERROS_HORARIO,
    description="Troca o horário da semana inteiro. O dia que não vier fica fechado.",
)
def salvar_semana(id_unidade: int, dados: HorarioSemana, db: Session = Depends(get_db), _: Usuario = GERENCIAR_UNIDADES):
    return horarios.salvar_semana(db, id_unidade, [d.model_dump() for d in dados.semana], _hoje())


@router.put(
    "/unidades/{id_unidade}/excecoes/{data}",
    response_model=HorarioSaida,
    responses=ERROS_HORARIO,
    description="Feriado ou horário especial de uma data; cria ou troca. Sem abre e fecha, a loja fica fechada.",
)
def salvar_excecao(
    id_unidade: int, data: date, dados: ExcecaoHorarioSalvar, db: Session = Depends(get_db), _: Usuario = GERENCIAR_UNIDADES,
):
    return horarios.salvar_excecao(db, id_unidade, data, dados.model_dump(), _hoje())


@router.delete(
    "/unidades/{id_unidade}/excecoes/{data}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**ERROS_HORARIO, 404: {"description": "Unidade não encontrada ou sem exceção nessa data"}},
)
def remover_excecao(id_unidade: int, data: date, db: Session = Depends(get_db), _: Usuario = GERENCIAR_UNIDADES):
    horarios.remover_excecao(db, id_unidade, data)
