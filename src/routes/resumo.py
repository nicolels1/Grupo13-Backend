from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.database.session import get_db
from src.dependencies.permissoes import get_usuario_ativo
from src.entities.comum import Marca
from src.entities.resumo import Faturamento, Resumo
from src.models.contas import Usuario
from src.use_cases import resumo

router = APIRouter(tags=["visão geral"])


@router.get(
    "/visao-geral/resumo",
    response_model=Resumo,
    # seção que a conta não pode ver fica ausente, e não null
    response_model_exclude_unset=True,
    responses={401: {"description": "Sem login válido"}, 403: {"description": "Só para contas internas"},
               404: {"description": "Unidade não encontrada"}, 422: {"description": "Referência no futuro"}},
    description="Números da Visão Geral, só das seções que a conta pode ver. Dias no horário de Brasília.",
)
def resumo_da_visao_geral(
    id_unidade: int | None = Query(None, description="Vazio: a rede inteira (rede_agora ignora o filtro)"),
    marca: Marca | None = Query(None, description="Vazio: as duas marcas. Vale para todas as seções"),
    referencia: date | None = Query(None, description="Último dia das janelas; vazio: hoje. Ex.: 2026-09-30 "
                                                      "para ver o histórico da Vulto, que vai até essa data"),
    usuario: Usuario = Depends(get_usuario_ativo),
    db: Session = Depends(get_db),
):
    return resumo.resumo(db, usuario, id_unidade, marca, referencia)


@router.get(
    "/visao-geral/faturamento",
    response_model=Faturamento,
    responses={401: {"description": "Sem login válido"}, 403: {"description": "Só o Admin"},
               404: {"description": "Unidade não encontrada"}, 422: {"description": "Fim antes do início"}},
    description="Faturamento por mês, unidade e canal da venda, com a regra dos três números da Vulto: venda paga "
                "sem o frete (à parte), menos as devoluções no mês em que saíram, sem cancelados. "
                "Meses no horário de Brasília.",
)
def faturamento(
    de: date = Query(description="Primeiro dia do período. Ex.: 2025-01-01"),
    ate: date = Query(description="Último dia do período, inteiro. Ex.: 2026-09-30"),
    id_unidade: int | None = Query(None, description="Vazio: todas as unidades"),
    marca: Marca | None = Query(None, description="Vazio: as duas marcas"),
    usuario: Usuario = Depends(get_usuario_ativo),
    db: Session = Depends(get_db),
):
    return resumo.faturamento(db, usuario, de, ate, id_unidade, marca)
