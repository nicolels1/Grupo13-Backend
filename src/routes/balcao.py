from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.database.session import get_db
from src.entities.atendimento import DevolucaoCriar, TrocaCriar
from src.entities.comum import LIMITE_MAXIMO, LIMITE_PADRAO, Lista, Pagina
from src.entities.estoque import Canal, EstoqueItem
from src.entities.vendas import PedidoSaida
from src.middlewares.permissoes import exige_permissao
from src.models.contas import Usuario
from src.use_cases import devolucoes, estoque

router = APIRouter(tags=["balcão"])

# troca e devolução na loja, sem chamado (ADR 0015); pelo Atendimento continua com atender_chamado
BALCAO = exige_permissao("registrar_troca_devolucao")
# consultar peça no balcão: quem vende ou troca vê o estoque das unidades, só para ler
CONSULTAR_PECA = exige_permissao("registrar_venda_fisica", "registrar_troca_devolucao")
ERROS = {
    401: {"description": "Sem login válido"},
    403: {"description": "Sem permissão"},
    404: {"description": "Pedido, loja, variante ou pagamento não encontrado"},
    422: {"description": "Fora do prazo de 30 dias, pedido não entregue, CD, peças a mais ou sem estoque"},
}


@router.get(
    "/balcao/pedidos",
    response_model=Lista[PedidoSaida],
    responses=ERROS,
    description="Um filtro só. Pelo CPF (da conta ou na nota): pedidos entregues nos últimos 30 dias.",
)
def buscar_pedidos(
    codigo_venda: str | None = Query(None, max_length=20, description="Código da notinha (CL...)"),
    id_pedido: int | None = Query(None, description="Número do pedido"),
    cpf: str | None = Query(None, description="Com ou sem máscara"),
    _: Usuario = Depends(BALCAO),
    db: Session = Depends(get_db),
):
    return {"items": devolucoes.buscar_no_balcao(db, codigo_venda, id_pedido, cpf)}


@router.post(
    "/balcao/pedidos/{id_pedido}/devolucao",
    response_model=PedidoSaida,
    responses=ERROS,
    description="A peça entra no estoque de loja física da loja; o estorno sai pelo mesmo meio de pagamento.",
)
def devolver(id_pedido: int, dados: DevolucaoCriar, usuario: Usuario = Depends(BALCAO),
             db: Session = Depends(get_db)):
    return devolucoes.devolver_no_balcao(db, usuario, id_pedido, dados.model_dump())


@router.post(
    "/balcao/pedidos/{id_pedido}/troca",
    response_model=PedidoSaida,
    responses=ERROS,
    description="A peça devolvida entra e a nova (mesmo produto, outra cor ou tamanho) sai da loja física.",
)
def trocar(id_pedido: int, dados: TrocaCriar, usuario: Usuario = Depends(BALCAO), db: Session = Depends(get_db)):
    return devolucoes.trocar_no_balcao(db, usuario, id_pedido, dados.model_dump())


@router.get(
    "/balcao/estoque",
    response_model=Pagina[EstoqueItem],
    responses=ERROS,
    description="Consultar peça: saldo e disponível de cada unidade e canal, só leitura. "
                "Para movimentar, as rotas de estoque continuam exigindo as permissões de estoque.",
)
def consultar_peca(
    busca: str | None = Query(None, description="Parte do SKU ou do nome do produto"),
    id_variante: int | None = None,
    id_unidade: int | None = None,
    canal: Canal | None = None,
    limit: int = Query(LIMITE_PADRAO, ge=1, le=LIMITE_MAXIMO),
    offset: int = Query(0, ge=0),
    _: Usuario = Depends(CONSULTAR_PECA),
    db: Session = Depends(get_db),
):
    return estoque.listar_estoque(
        db, limit, offset, id_unidade=id_unidade, id_variante=id_variante, canal=canal, busca=busca,
    )
