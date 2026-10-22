from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.database.session import get_db
from src.dependencies.permissoes import exige_permissao
from src.entities.comum import Lista
from src.entities.migracao import OrigemSaida
from src.models.contas import Usuario
from src.use_cases import migracao

router = APIRouter(tags=["migração da Vulto"])

# quem vê pedido, chamado, peça ou estoque vê também de onde o registro migrado veio
VER_ORIGEM = exige_permissao(
    "registrar_venda_fisica", "preparar_entregar_pedido", "cancelar_pedido_equipe", "corrigir_cadastro_cliente",
    "registrar_troca_devolucao", "atender_chamado", "gerenciar_catalogo", "movimentar_estoque",
)


@router.get(
    "/migracao/origem",
    response_model=Lista[OrigemSaida],
    responses={401: {"description": "Sem login válido"}, 403: {"description": "Sem permissão"}},
    description="De onde veio um registro migrado da Vulto e o que a migração mudou nele, com a decisão do "
                "registro de decisões. Lista vazia: o registro não veio da migração (P4).",
)
def origem_do_registro(
    tabela: str = Query(pattern="^[a-z_]+$", max_length=40, description="Tabela da plataforma, ex.: pedido"),
    id_registro: str = Query(max_length=40, description="Id do registro (número ou UUID)"),
    _: Usuario = Depends(VER_ORIGEM),
    db: Session = Depends(get_db),
):
    return {"items": migracao.origem_do_registro(db, tabela, id_registro)}
