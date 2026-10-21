import uuid
from datetime import date, datetime, timezone
from decimal import ROUND_DOWN, Decimal

from sqlalchemy.orm import Session

from src.models.fidelidade import MovimentoPontos, RegraFidelidade
from src.models.vendas import Pedido
from src.repositories import cliente_sem_conta_repository, usuario_repository
from src.repositories import fidelidade_repository as repo
from src.use_cases.erros import Conflito, RecursoNaoEncontrado, RegraDeNegocio
from src.use_cases.estoque import BRASILIA

CENTAVO = Decimal("0.01")

# programa de fidelidade de cada marca (P5, P10): as regras têm data e valem dali em diante; o saldo, a
# categoria e o FIFO dos pontos ficam no banco (trigger aplica_movimento_pontos, migration 3532b648a2ad)


def hoje() -> date:
    return datetime.now(timezone.utc).astimezone(BRASILIA).date()


# a vigente de cada marca é a de data mais recente até hoje; na mesma data, a criada por último
def listar_regras(db: Session, marca: str | None = None) -> list[dict]:
    regras = repo.listar_regras(db, marca)
    vigentes = {}
    for regra in regras:  # já vêm da mais nova para a mais antiga em cada marca
        if regra.valida_desde <= hoje():
            vigentes.setdefault(regra.marca, regra.id_regra)
    colunas = RegraFidelidade.__table__.columns
    return [{**{c.key: getattr(r, c.key) for c in colunas}, "vigente": vigentes.get(r.marca) == r.id_regra}
            for r in regras]


# a regra nova nunca muda um ponto já ganho: só vale de amanhã em diante (o banco também recusa)
def criar_regra(db: Session, id_usuario: uuid.UUID, dados: dict) -> dict:
    if dados["valida_desde"] <= hoje():
        raise RegraDeNegocio("A regra nova vale a partir de amanhã ou de uma data futura")
    regra = RegraFidelidade(**dados, id_criado_por=id_usuario)
    db.add(regra)
    db.commit()
    db.refresh(regra)
    return next(r for r in listar_regras(db, regra.marca) if r["id_regra"] == regra.id_regra)


# ---------- resgate ----------

# dono dos pontos pelo CPF dado no caixa: a conta ou, sem ela, o cliente da Vulto que ainda não criou conta (P2)
def dono_pelo_cpf(db: Session, cpf: str) -> tuple[uuid.UUID | None, int | None]:
    cliente = usuario_repository.buscar_cliente_por_cpf(db, cpf)
    if cliente is not None:
        return cliente.id_usuario, None
    sem_conta = cliente_sem_conta_repository.buscar_por_cpf(db, cpf)
    if sem_conta is not None:
        return None, sem_conta.id_cliente_sem_conta
    raise RecursoNaoEncontrado("Nenhum cliente com pontos neste CPF")


# confere o resgate sem gastar: o mínimo da regra, o saldo e o desconto menor que o valor das peças. O desconto
# é o valor dos pontos mais antigos primeiro (FIFO), cada um pelo valor da época em que foi ganho (K3)
def simular_resgate(db: Session, id_cliente, id_cliente_sem_conta, marca: str, pontos: int,
                    valor_itens: Decimal | None = None) -> dict:
    regra = repo.regra_vigente(db, marca, hoje())
    if regra is None:
        raise RegraDeNegocio("Esta marca não tem programa de fidelidade")
    if pontos < regra.resgate_minimo:
        raise RegraDeNegocio(f"O resgate mínimo é de {regra.resgate_minimo} pontos")
    disponivel = repo.saldo(db, id_cliente, id_cliente_sem_conta, marca)
    if pontos > disponivel:
        raise RegraDeNegocio(f"Pontos insuficientes: o saldo é de {disponivel} pontos")
    desconto = Decimal(repo.valor_do_resgate(db, id_cliente, id_cliente_sem_conta, marca, pontos))
    desconto = desconto.quantize(CENTAVO, ROUND_DOWN)
    if valor_itens is not None and desconto >= valor_itens:
        raise RegraDeNegocio("O desconto dos pontos precisa ser menor que o valor das peças")
    return {"pontos": pontos, "desconto": desconto, "saldo": disponivel, "resgate_minimo": regra.resgate_minimo}


# gasta os pontos no pedido já gravado: o trigger tira dos lotes mais antigos, e o desconto é o que eles valiam.
# O pedido guarda o desconto (V18) e o total cai; o crédito do pedido sai sobre o valor pago, sem o desconto
def resgatar(db: Session, pedido: Pedido, marca: str, pontos: int, valor_itens: Decimal,
             id_registrado_por: uuid.UUID | None = None) -> Decimal:
    previsto = simular_resgate(db, pedido.id_cliente, pedido.id_cliente_sem_conta, marca, pontos, valor_itens)
    movimento = MovimentoPontos(
        id_cliente=pedido.id_cliente, id_cliente_sem_conta=pedido.id_cliente_sem_conta, marca=marca, tipo="resgate",
        pontos=-pontos, origem="plataforma", id_pedido=pedido.id_pedido, id_registrado_por=id_registrado_por,
    )
    db.add(movimento)
    db.flush()
    desconto = Decimal(repo.valor_gasto(db, movimento.id_movimento)).quantize(CENTAVO, ROUND_DOWN)
    if desconto != previsto["desconto"]:
        raise Conflito("O saldo de pontos mudou enquanto a compra era fechada: confira de novo")
    pedido.valor_desconto = desconto
    pedido.valor_total -= desconto
    db.flush()
    return desconto


# ---------- saldo, extrato e passivo ----------

def pontos_do_cliente(db: Session, id_cliente=None, id_cliente_sem_conta=None) -> dict:
    return {"saldos": repo.resumo(db, id_cliente, id_cliente_sem_conta)}


def extrato(db: Session, limit: int, offset: int, marca: str | None = None, id_cliente=None,
            id_cliente_sem_conta=None) -> dict:
    itens, total = repo.extrato(db, id_cliente, id_cliente_sem_conta, marca, limit, offset)
    return {"items": itens, "total": total, "limit": limit, "offset": offset}


# o quanto cada marca deve em pontos (Entrega 3): o que sobra nos lotes, cada ponto pelo valor da época
def passivo(db: Session) -> list[dict]:
    marcas = {}
    for linha in repo.passivo_por_valor(db):
        reais = linha["pontos"] * linha["valor_ponto"]
        marca = marcas.setdefault(linha["marca"], {"marca": linha["marca"], "pontos": 0, "reais": Decimal("0"),
                                                   "por_valor": []})
        marca["pontos"] += linha["pontos"]
        marca["reais"] += reais
        marca["por_valor"].append({"valor_ponto": linha["valor_ponto"], "pontos": linha["pontos"], "reais": reais})
    return list(marcas.values())
