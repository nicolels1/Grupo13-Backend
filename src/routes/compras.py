from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from src.database.session import get_db
from src.dependencies.permissoes import exige_cliente
from src.entities.vendas import CheckoutCompra, CobrancaCriar, CompraSaida, RespostaGateway
from src.models.contas import Usuario
from src.use_cases import compras

# compra do site: um checkout, uma cobrança e uma confirmação para o cliente; um pedido por marca por dentro
router = APIRouter(tags=["compras"])

ERROS = {
    401: {"description": "Sem login válido"},
    403: {"description": "Só para contas de cliente"},
    404: {"description": "Compra, endereço, loja ou variante não encontrado"},
    409: {"description": "Cobrança já respondida"},
    422: {"description": "Dados inválidos ou regra da venda (ex.: sem estoque, reserva vencida)"},
}


@router.post(
    "/compras",
    status_code=status.HTTP_201_CREATED,
    response_model=CompraSaida,
    responses=ERROS,
    description="Checkout do carrinho do site: reserva as peças por 15 minutos e cria a compra com um pedido por "
                "marca, cada um numa unidade dela, tudo ou nada (ADR 0017). Com as duas marcas, só entrega em casa. "
                "Os pontos de cada marca (`pontos_por_marca`) viram desconto no pedido dela.",
)
def fazer_compra(dados: CheckoutCompra, cliente: Usuario = Depends(exige_cliente), db: Session = Depends(get_db)):
    return compras.checkout(db, cliente, dados.model_dump())


@router.get("/compras/{id_compra}", response_model=CompraSaida, responses=ERROS)
def minha_compra(id_compra: int, cliente: Usuario = Depends(exige_cliente), db: Session = Depends(get_db)):
    return compras.detalhar_do_cliente(db, cliente, id_compra)


@router.post(
    "/compras/{id_compra}/pagamentos",
    status_code=status.HTTP_201_CREATED,
    response_model=CompraSaida,
    responses=ERROS,
    description="Cria a cobrança única da compra no gateway simulado (fica pendente até a resposta). Pedir de novo "
                "com o mesmo método devolve a mesma cobrança; outro método recusa a anterior.",
)
def pagar(
    id_compra: int, dados: CobrancaCriar, cliente: Usuario = Depends(exige_cliente), db: Session = Depends(get_db),
):
    return compras.criar_cobranca(db, cliente, id_compra, dados.metodo)


@router.post(
    "/compras/{id_compra}/simular",
    response_model=CompraSaida,
    responses=ERROS,
    description="Gateway de pagamento simulado: aprova ou recusa a cobrança pendente, uma vez só. Aprovada, todos "
                "os pedidos ficam pagos e o estoque baixa; se a reserva venceu, nada é cobrado e a compra é cancelada.",
)
def simular_gateway(
    id_compra: int, dados: RespostaGateway, cliente: Usuario = Depends(exige_cliente),
    db: Session = Depends(get_db),
):
    return compras.responder_cobranca(db, cliente, id_compra, dados.aprovado)


@router.post(
    "/compras/{id_compra}/cancelar",
    response_model=CompraSaida,
    responses=ERROS,
    description="O cliente cancela a compra inteira até o pagamento; depois disso, cada pedido pelo atendimento.",
)
def cancelar(id_compra: int, cliente: Usuario = Depends(exige_cliente), db: Session = Depends(get_db)):
    return compras.cancelar_pelo_cliente(db, cliente, id_compra)
