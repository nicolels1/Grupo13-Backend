import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from src.database.session import get_db
from src.entities.comum import LIMITE_MAXIMO, LIMITE_PADRAO, Pagina, campos_alterados
from src.entities.vendas import (
    CancelamentoEquipe, ClienteCorrigido, ClienteCorrigir, ClienteResumo, Entrega, Modalidade,
    PedidoSaida, StatusPedido, VendaFisica,
)
from src.middlewares.permissoes import exige_permissao
from src.models.contas import Usuario
from src.routes.contas import get_supabase_admin
from src.use_cases import clientes, pedidos, vendas
from src.utils.supabase_admin import SupabaseAdmin

router = APIRouter(tags=["vendas"])

# a página Pedidos aparece para quem tem alguma permissão de vendas; o atendente também consulta
VER_PEDIDOS = exige_permissao(
    "registrar_venda_fisica", "preparar_entregar_pedido", "cancelar_pedido_equipe", "corrigir_cadastro_cliente",
    "registrar_troca_devolucao", "atender_chamado",
)
PREPARAR = exige_permissao("preparar_entregar_pedido")
ATENDER_NO_CAIXA = exige_permissao("registrar_venda_fisica", "corrigir_cadastro_cliente")
ERROS = {
    401: {"description": "Sem login válido"},
    403: {"description": "Sem permissão"},
    404: {"description": "Pedido, unidade, cliente ou variante não encontrado"},
    409: {"description": "E-mail ou CPF já cadastrado"},
    422: {"description": "Dados inválidos ou etapa errada do pedido"},
}


# ---------- pedidos (plataforma interna) ----------

@router.get("/vendas/pedidos", response_model=Pagina[PedidoSaida], responses=ERROS)
def listar(
    status_pedido: StatusPedido | None = Query(None, alias="status"),
    canal: str | None = Query(None, pattern="^(loja_fisica|online)$"),
    modalidade: Modalidade | None = None,
    id_unidade: int | None = None,
    codigo_venda: str | None = Query(None, max_length=20),
    pronto_ha_mais_de_dias: int | None = Query(
        None, ge=0, description="Retiradas prontas há mais de N dias (risco de vencer)",
    ),
    de: str | None = Query(None, description="Criados a partir de AAAA-MM-DD (começo do dia) ou AAAA-MM-DDTHH:MM"),
    ate: str | None = Query(None, description="Criados até AAAA-MM-DD (fim do dia) ou AAAA-MM-DDTHH:MM"),
    limit: int = Query(LIMITE_PADRAO, ge=1, le=LIMITE_MAXIMO),
    offset: int = Query(0, ge=0),
    _: Usuario = Depends(VER_PEDIDOS),
    db: Session = Depends(get_db),
):
    return vendas.listar(
        db, limit, offset, pronto_ha_mais_de_dias, de, ate, status=status_pedido, canal=canal, modalidade=modalidade,
        id_unidade=id_unidade, codigo_venda=codigo_venda,
    )


@router.get("/vendas/pedidos/{id_pedido}", response_model=PedidoSaida, responses=ERROS)
def detalhar(id_pedido: int, _: Usuario = Depends(VER_PEDIDOS), db: Session = Depends(get_db)):
    return pedidos.detalhar(db, id_pedido)


@router.post(
    "/vendas/pedidos",
    status_code=status.HTTP_201_CREATED,
    response_model=PedidoSaida,
    responses=ERROS,
    description="Venda física: nasce paga e entregue e baixa o estoque de loja física. "
                "Os pagamentos precisam somar o total. O caixa não cria conta: CPF com conta liga o pedido; "
                "CPF sem conta fica no pedido (cpf_nota) até o cadastro pelo site.",
)
def registrar_venda_fisica(
    dados: VendaFisica, vendedor: Usuario = Depends(exige_permissao("registrar_venda_fisica")),
    db: Session = Depends(get_db),
):
    return vendas.registrar_venda_fisica(db, vendedor, dados.model_dump())


@router.post("/vendas/pedidos/{id_pedido}/enviar", response_model=PedidoSaida, responses=ERROS)
def enviar(id_pedido: int, _: Usuario = Depends(PREPARAR), db: Session = Depends(get_db)):
    return vendas.enviar(db, id_pedido)


@router.post(
    "/vendas/pedidos/{id_pedido}/pronto-retirada",
    response_model=PedidoSaida,
    responses=ERROS,
    description="A partir daqui, o cliente tem 7 dias para retirar; depois, o pedido é cancelado com estorno.",
)
def pronto_para_retirada(id_pedido: int, _: Usuario = Depends(PREPARAR), db: Session = Depends(get_db)):
    return vendas.marcar_pronto_para_retirada(db, id_pedido)


@router.post(
    "/vendas/pedidos/{id_pedido}/entregar",
    response_model=PedidoSaida,
    responses=ERROS,
    description="Entrega do pedido enviado ou retirada na loja (na retirada, confere o código do pedido).",
)
def entregar(id_pedido: int, dados: Entrega, _: Usuario = Depends(PREPARAR), db: Session = Depends(get_db)):
    return vendas.entregar(db, id_pedido, dados.codigo_venda)


@router.post(
    "/vendas/pedidos/{id_pedido}/cancelar",
    response_model=PedidoSaida,
    responses=ERROS,
    description="Antes do envio. Já pago: estorna tudo e as peças voltam ao estoque online.",
)
def cancelar(
    id_pedido: int, dados: CancelamentoEquipe,
    usuario: Usuario = Depends(exige_permissao("cancelar_pedido_equipe")), db: Session = Depends(get_db),
):
    return vendas.cancelar(db, usuario, id_pedido, dados.justificativa)


# ---------- clientes no caixa ----------

@router.get("/vendas/clientes", response_model=ClienteResumo, responses=ERROS)
def buscar_cliente(
    cpf: str = Query(description="Com ou sem máscara"), _: Usuario = Depends(ATENDER_NO_CAIXA),
    db: Session = Depends(get_db),
):
    return clientes.buscar_por_cpf(db, cpf)


@router.patch(
    "/vendas/clientes/{id_usuario}",
    response_model=ClienteCorrigido,
    responses=ERROS,
    description="Corrige e-mail ou CPF com documento. Se o CPF certo já tem conta, os pedidos passam "
                "para ela e esta conta é desativada. As compras da loja com o CPF certo na nota vão para a conta.",
)
def corrigir_cliente(
    id_usuario: uuid.UUID, dados: ClienteCorrigir,
    _: Usuario = Depends(exige_permissao("corrigir_cadastro_cliente")), db: Session = Depends(get_db),
    auth: SupabaseAdmin = Depends(get_supabase_admin),
):
    return clientes.corrigir(db, auth, id_usuario, campos_alterados(dados))
