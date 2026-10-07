from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from src.database.session import get_db
from src.entities.comum import LIMITE_MAXIMO, LIMITE_PADRAO, Pagina
from src.entities.contas import UsuarioSaida
from src.entities.vendas import (
    Ativacao, Carrinho, Checkout, CobrancaCriar, PedidoSaida, Reivindicacao, RespostaGateway, ResumoCarrinho,
    StatusPedido,
)
from src.middlewares.auth import get_current_user
from src.middlewares.permissoes import exige_cliente
from src.models.contas import Usuario
from src.routes.contas import get_supabase_admin
from src.use_cases import clientes, pedidos
from src.utils.supabase_admin import SupabaseAdmin

router = APIRouter(tags=["pedidos"])

ERROS = {
    401: {"description": "Sem login válido"},
    403: {"description": "Só para contas de cliente"},
    404: {"description": "Pedido, endereço, loja ou variante não encontrado"},
    409: {"description": "Cobrança já respondida ou compra já reivindicada"},
    422: {"description": "Dados inválidos ou regra da venda (ex.: sem estoque, reserva vencida)"},
}


# público: a vitrine monta o carrinho antes do login
@router.post(
    "/carrinho",
    response_model=ResumoCarrinho,
    responses={404: ERROS[404], 422: ERROS[422]},
    description="Preços do momento, frete da entrega em casa e lojas que têm tudo para retirada. Não reserva nada.",
)
def resumo_do_carrinho(dados: Carrinho, db: Session = Depends(get_db)):
    return pedidos.resumo_carrinho(db, dados.model_dump()["itens"])


# ---------- plataforma do cliente ----------

@router.post(
    "/pedidos",
    status_code=status.HTTP_201_CREATED,
    response_model=PedidoSaida,
    responses=ERROS,
    description="Checkout: reserva as peças por 15 minutos e cria o pedido aguardando pagamento.",
)
def fazer_pedido(dados: Checkout, cliente: Usuario = Depends(exige_cliente), db: Session = Depends(get_db)):
    return pedidos.checkout(db, cliente, dados.model_dump())


@router.get("/pedidos", response_model=Pagina[PedidoSaida], responses=ERROS)
def meus_pedidos(
    status_pedido: StatusPedido | None = Query(None, alias="status"),
    limit: int = Query(LIMITE_PADRAO, ge=1, le=LIMITE_MAXIMO),
    offset: int = Query(0, ge=0),
    cliente: Usuario = Depends(exige_cliente),
    db: Session = Depends(get_db),
):
    return pedidos.listar_do_cliente(db, cliente, limit, offset, status_pedido)


@router.get("/pedidos/{id_pedido}", response_model=PedidoSaida, responses=ERROS)
def meu_pedido(id_pedido: int, cliente: Usuario = Depends(exige_cliente), db: Session = Depends(get_db)):
    return pedidos.detalhar_do_cliente(db, cliente, id_pedido)


@router.post(
    "/pedidos/{id_pedido}/pagamentos",
    status_code=status.HTTP_201_CREATED,
    response_model=PedidoSaida,
    responses=ERROS,
    description="Cria a cobrança do valor que falta no gateway simulado (fica pendente até a resposta).",
)
def pagar(
    id_pedido: int, dados: CobrancaCriar, cliente: Usuario = Depends(exige_cliente), db: Session = Depends(get_db),
):
    return pedidos.criar_cobranca(db, cliente, id_pedido, dados.metodo)


@router.post(
    "/pagamentos/{id_pagamento}/simular",
    response_model=PedidoSaida,
    responses=ERROS,
    description="Gateway de pagamento simulado: aprova ou recusa a cobrança pendente. Aprovado e cobrindo o "
                "total, o pedido fica pago e o estoque baixa; depois do cancelamento, o valor é estornado.",
)
def simular_gateway(
    id_pagamento: int, dados: RespostaGateway, cliente: Usuario = Depends(exige_cliente),
    db: Session = Depends(get_db),
):
    return pedidos.responder_cobranca(db, cliente, id_pagamento, dados.aprovado)


@router.post(
    "/pedidos/{id_pedido}/cancelar",
    response_model=PedidoSaida,
    responses=ERROS,
    description="O cliente cancela até o pagamento; depois disso, pelo atendimento.",
)
def cancelar(id_pedido: int, cliente: Usuario = Depends(exige_cliente), db: Session = Depends(get_db)):
    return pedidos.cancelar_pelo_cliente(db, cliente, id_pedido)


@router.post(
    "/pedidos/reivindicar",
    response_model=PedidoSaida,
    responses=ERROS,
    description="Liga à conta uma compra feita na loja sem CPF, pelo código do comprovante. Uma única vez.",
)
def reivindicar(dados: Reivindicacao, cliente: Usuario = Depends(exige_cliente), db: Session = Depends(get_db)):
    return pedidos.reivindicar(db, cliente, dados.codigo_venda)


# a conta criada no caixa entra pelo link do Supabase e ainda não está ativa: por isso usa só o token
@router.post(
    "/ativacao",
    response_model=UsuarioSaida,
    responses={401: ERROS[401], 403: {"description": "Usuário não cadastrado"},
               422: {"description": "CPF não confere ou conta já ativada"}},
    description="Primeiro acesso da conta criada no caixa: confirma o CPF e define a senha.",
)
def ativar_conta(
    dados: Ativacao, id_usuario: str = Depends(get_current_user), db: Session = Depends(get_db),
    auth: SupabaseAdmin = Depends(get_supabase_admin),
):
    return clientes.ativar(db, auth, id_usuario, dados.cpf, dados.senha)
