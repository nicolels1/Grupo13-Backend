import secrets
from decimal import Decimal

from sqlalchemy.orm import Session

from src.models.contas import Usuario
from src.models.vendas import Compra, Pagamento, Pedido
from src.repositories import compra_repository as repo
from src.repositories import pedido_repository
from src.use_cases import fidelidade, pagamentos, pedidos
from src.use_cases.erros import Conflito, RecursoNaoEncontrado, RegraDeNegocio

# Compra do site (ADR 0017): o cliente fecha o carrinho das duas marcas, paga uma vez só e cada marca sai num
# pedido próprio, de uma unidade dela. A cobrança única fica na compra; aprovada, cada pedido ganha o seu
# pagamento, e o faturamento, o caixa e os pontos continuam por pedido. Pagar, cancelar e vencer travam a
# compra antes dos pedidos, na mesma ordem do pg_cron
NAO_ENCONTRADA = "Compra não encontrada"
NOMES_DAS_MARCAS = {"lorenzi": "Casa Lorenzi", "vulto": "Vulto"}
ZERO = Decimal("0.00")


def novo_codigo_compra() -> str:
    return "CP" + "".join(secrets.choice(pedidos.ALFABETO_DO_CODIGO) for _ in range(8))


# ---------- montagem da resposta ----------

def detalhar(db: Session, id_compra: int) -> dict:
    compra = repo.buscar_compra(db, id_compra)
    if compra is None:
        raise RecursoNaoEncontrado(NAO_ENCONTRADA)
    da_compra = pedidos.montar(db, repo.pedidos_com_nomes(db, id_compra))
    dados = {coluna.key: getattr(compra, coluna.key) for coluna in Compra.__table__.columns}
    dados["pedidos"] = da_compra
    for campo in ("valor_itens", "valor_frete", "valor_desconto"):
        dados[campo] = sum((p[campo] for p in da_compra), ZERO)
    return dados


# compra de outra pessoa responde como inexistente: trocar o número na URL não revela nada
def do_cliente(db: Session, cliente: Usuario, id_compra: int, travar: bool = False) -> Compra:
    compra = repo.buscar_compra(db, id_compra, travar)
    if compra is None or compra.id_cliente != cliente.id_usuario:
        raise RecursoNaoEncontrado(NAO_ENCONTRADA)
    return compra


def detalhar_do_cliente(db: Session, cliente: Usuario, id_compra: int) -> dict:
    do_cliente(db, cliente, id_compra)
    return detalhar(db, id_compra)


def reserva_vencida(compra: Compra) -> bool:
    return compra.status == "aguardando_pagamento" and compra.reserva_expira_em <= pedidos.agora()


# libera a reserva dos pedidos que ainda esperam pagamento e cancela a compra; a cobrança pendente expira junto
def _cancelar(db: Session, compra: Compra, da_compra: list[Pedido], motivo: str, **autor) -> None:
    lancamentos = pedido_repository.pagamentos_dos_pedidos(db, [p.id_pedido for p in da_compra])
    for pedido in da_compra:
        if pedido.status == "aguardando_pagamento":
            pedidos.cancelar_aguardando(db, pedido, lancamentos[pedido.id_pedido], motivo, **autor)
    compra.status = "cancelado"
    compra.cancelado_em = pedidos.agora()
    if compra.status_cobranca == "pendente":
        compra.status_cobranca = "recusado"


# ---------- checkout ----------

# escolhe a unidade de cada marca, reserva as peças por 15 minutos e cria a compra com um pedido por marca,
# tudo na mesma transação: se uma marca não tem as peças, nada é reservado. Com as duas marcas, só entrega em
# casa: cada loja vende só a sua. Cada marca tem o seu programa de pontos, então os pontos de cada uma viram
# desconto no pedido dela, com as regras de sempre (mínimo, saldo e desconto menor que as peças da marca)
def checkout(db: Session, cliente: Usuario, dados: dict) -> dict:
    resumo = pedidos.itens_a_venda(db, dados["itens"])
    grupos = pedidos.por_marca(resumo)
    pontos = dados.get("pontos_por_marca") or {}
    if len(grupos) > 1 and dados["modalidade"] == "retirada":
        raise RegraDeNegocio("Retirada na loja só com peças de uma marca: cada loja vende só a sua. "
                             "Escolha a entrega em casa")
    for marca in pontos:
        if marca not in grupos:
            nome = NOMES_DAS_MARCAS[marca]
            raise RegraDeNegocio(f"Os pontos da {nome} só valem com peças da {nome} no carrinho")

    # confere todas as marcas (peças e pontos) antes de reservar qualquer peça
    escolhidas = []
    for marca, da_marca in grupos.items():
        try:
            unidade, endereco = pedidos.unidade_do_checkout(db, cliente, dados, pedidos.quantidades(da_marca))
            if pontos.get(marca):
                valor_da_marca = sum((i["subtotal"] for i in da_marca), ZERO)
                fidelidade.simular_resgate(db, cliente.id_usuario, None, marca, pontos[marca], valor_da_marca)
        except RegraDeNegocio as erro:
            if len(grupos) == 1:
                raise
            raise RegraDeNegocio(f"Peças da {NOMES_DAS_MARCAS[marca]}: {erro}") from erro
        escolhidas.append((marca, unidade["id_unidade"], endereco, da_marca))

    expira_em = pedidos.agora() + pedidos.RESERVA
    compra = Compra(codigo_compra=novo_codigo_compra(), id_cliente=cliente.id_usuario, status="aguardando_pagamento",
                    valor_total=ZERO, reserva_expira_em=expira_em)
    db.add(compra)
    db.flush()
    criados = [
        pedidos.criar_pedido(db, cliente, id_unidade, dados["modalidade"], da_marca, endereco,
                             pontos.get(marca), compra.id_compra, expira_em)
        for marca, id_unidade, endereco, da_marca in escolhidas
    ]
    compra.valor_total = sum((p.valor_total for p in criados), ZERO)
    db.commit()
    return detalhar(db, compra.id_compra)


# ---------- pagamento ----------

# cobrança única da compra no gateway simulado. Pedir de novo com o mesmo método devolve a mesma cobrança
# (clicar duas vezes não gera duas); trocar de método recusa a anterior e gera outra transação
def criar_cobranca(db: Session, cliente: Usuario, id_compra: int, metodo: str) -> dict:
    compra = do_cliente(db, cliente, id_compra, travar=True)
    if compra.status != "aguardando_pagamento":
        raise RegraDeNegocio("Esta compra não está aguardando pagamento")
    if reserva_vencida(compra):
        raise RegraDeNegocio("A reserva de 15 minutos venceu: a compra será cancelada. Faça uma nova compra")
    if compra.status_cobranca == "pendente" and compra.metodo == metodo:
        return detalhar(db, id_compra)
    compra.metodo = metodo
    compra.id_transacao_gateway = pagamentos.id_transacao(metodo)
    compra.status_cobranca = "pendente"
    db.commit()
    return detalhar(db, id_compra)


# resposta do gateway simulado, uma vez por cobrança. Aprovada: cada pedido ganha o seu pagamento (o trigger de
# pontos credita cada marca uma vez), o estoque das duas marcas baixa e tudo fica pago, ou nada fica. Se a
# reserva venceu antes da resposta, nada é cobrado e a compra é cancelada, como o pg_cron faria (ADR 0002)
def responder_cobranca(db: Session, cliente: Usuario, id_compra: int, aprovado: bool) -> dict:
    compra = do_cliente(db, cliente, id_compra, travar=True)
    if compra.status_cobranca != "pendente":
        raise Conflito("Esta cobrança já foi respondida")
    da_compra = repo.pedidos_da_compra(db, id_compra, travar=True)

    if not aprovado:
        compra.status_cobranca = "recusado"
    elif reserva_vencida(compra):
        _cancelar(db, compra, da_compra, "reserva_vencida")
    else:
        if any(p.status != "aguardando_pagamento" for p in da_compra):
            raise Conflito("Os pedidos desta compra mudaram: faça uma nova compra")
        lancamentos = pedido_repository.pagamentos_dos_pedidos(db, [p.id_pedido for p in da_compra])
        pago_em = pedidos.agora()
        for pedido in da_compra:
            falta = pedido.valor_total - pagamentos.valor_pago(lancamentos[pedido.id_pedido])
            if falta > 0:
                db.add(Pagamento(
                    id_pedido=pedido.id_pedido, tipo="pagamento", metodo=compra.metodo, valor=falta, status="aprovado",
                    id_transacao_gateway=f"{compra.id_transacao_gateway}_{pedido.id_pedido}",
                ))
            pedidos.baixar_venda_online(
                db, pedido, pedidos.quantidades(pedido_repository.itens_do_pedido(db, pedido.id_pedido)))
            pedido.status = "pago"
            pedido.pago_em = pago_em
        compra.status_cobranca = "aprovado"
        compra.status = "pago"
        compra.pago_em = pago_em
    db.commit()
    return detalhar(db, id_compra)


# ---------- cancelamento ----------

# o cliente cancela a compra inteira até o pagamento; depois disso, cada pedido pelo atendimento
def cancelar_pelo_cliente(db: Session, cliente: Usuario, id_compra: int) -> dict:
    compra = do_cliente(db, cliente, id_compra, travar=True)
    if compra.status != "aguardando_pagamento":
        raise RegraDeNegocio("Só dá para cancelar antes do pagamento; depois, abra um chamado")
    _cancelar(db, compra, repo.pedidos_da_compra(db, id_compra, travar=True), "cliente",
              id_cancelado_por=cliente.id_usuario)
    db.commit()
    return detalhar(db, id_compra)


# a equipe cancela um pedido de compra ainda sem pagamento: a compra inteira cancela, porque a cobrança é uma
# só. Devolve False se a compra já não espera pagamento (aí o pedido segue o cancelamento de sempre)
def cancelar_pela_equipe(db: Session, id_compra: int, id_usuario, justificativa: str) -> bool:
    compra = repo.buscar_compra(db, id_compra, travar=True)
    if compra is None or compra.status != "aguardando_pagamento":
        return False
    _cancelar(db, compra, repo.pedidos_da_compra(db, id_compra, travar=True), "equipe",
              id_cancelado_por=id_usuario, justificativa=justificativa)
    db.commit()
    return True
