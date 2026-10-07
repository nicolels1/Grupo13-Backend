import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from src.models.vendas import Pagamento, Pedido
from src.use_cases.erros import RegraDeNegocio

ZERO = Decimal("0.00")


# não há gateway real no case: cada transação online ganha um id do gateway simulado
def id_transacao(metodo: str) -> str | None:
    return None if metodo == "dinheiro" else f"sim_{uuid.uuid4().hex}"


# o pedido está pago quando aprovados − estornos aprovados cobrem o total (ADR 0003)
def valor_pago(pagamentos: list[Pagamento]) -> Decimal:
    aprovados = [p for p in pagamentos if p.status == "aprovado"]
    entrou = sum((p.valor for p in aprovados if p.tipo == "pagamento"), ZERO)
    saiu = sum((p.valor for p in aprovados if p.tipo == "estorno"), ZERO)
    return entrou - saiu


# quanto ainda pode ser estornado de um pagamento: o valor menos os estornos que não foram recusados
def restante_estornavel(original: Pagamento, pagamentos: list[Pagamento]) -> Decimal:
    estornado = sum(
        (p.valor for p in pagamentos if p.id_pagamento_original == original.id_pagamento and p.status != "recusado"),
        ZERO,
    )
    return original.valor - estornado


# estorno é um lançamento próprio, ligado ao original e volta pelo mesmo método (ADR 0003);
# o gateway simulado aprova na hora
def estornar(db: Session, pedido: Pedido, original: Pagamento, valor: Decimal, pagamentos: list[Pagamento],
             id_chamado: int | None = None) -> Pagamento:
    if original.id_pedido != pedido.id_pedido or original.tipo != "pagamento" or original.status != "aprovado":
        raise RegraDeNegocio("Só um pagamento aprovado deste pedido pode ser estornado")
    if valor <= 0:
        raise RegraDeNegocio("O valor do estorno precisa ser positivo")
    restante = restante_estornavel(original, pagamentos)
    if valor > restante:
        raise RegraDeNegocio(f"Estorno maior que o disponível nesse pagamento: R$ {restante}")
    estorno = Pagamento(
        id_pedido=pedido.id_pedido, tipo="estorno", id_pagamento_original=original.id_pagamento,
        id_chamado=id_chamado, metodo=original.metodo, id_transacao_gateway=id_transacao(original.metodo),
        valor=valor, status="aprovado",
    )
    db.add(estorno)
    pagamentos.append(estorno)
    return estorno


# cancelamento depois de pago: devolve tudo o que ainda não foi estornado, pagamento por pagamento
def estornar_tudo(db: Session, pedido: Pedido, pagamentos: list[Pagamento]) -> list[Pagamento]:
    estornos = []
    for original in [p for p in pagamentos if p.tipo == "pagamento" and p.status == "aprovado"]:
        restante = restante_estornavel(original, pagamentos)
        if restante > 0:
            estornos.append(estornar(db, pedido, original, restante, pagamentos))
    return estornos


# a cobrança pendente expira junto com a reserva (ADR 0002)
def recusar_pendentes(pagamentos: list[Pagamento]) -> None:
    for pagamento in pagamentos:
        if pagamento.status == "pendente":
            pagamento.status = "recusado"
