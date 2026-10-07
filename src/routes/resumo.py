from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.database.session import get_db
from src.entities.resumo import Resumo
from src.middlewares.permissoes import get_usuario_ativo
from src.models.contas import Usuario
from src.use_cases import resumo

router = APIRouter(tags=["visão geral"])


@router.get(
    "/visao-geral/resumo",
    response_model=Resumo,
    # seção que a conta não pode ver fica ausente, e não null
    response_model_exclude_unset=True,
    responses={401: {"description": "Sem login válido"}, 403: {"description": "Só para contas internas"},
               404: {"description": "Unidade não encontrada"}},
    description="Números da Visão Geral, só das seções que a conta pode ver. Dias no horário de Brasília.",
)
def resumo_da_visao_geral(
    id_unidade: int | None = Query(None, description="Vazio: a rede inteira (rede_agora ignora o filtro)"),
    usuario: Usuario = Depends(get_usuario_ativo),
    db: Session = Depends(get_db),
):
    return resumo.resumo(db, usuario, id_unidade)
