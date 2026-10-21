from decimal import Decimal

from sqlalchemy import Select, and_, func, select
from sqlalchemy.orm import Session

from src.models.fidelidade import ConsumoPontos, LotePontos, RegraFidelidade, SaldoPontos


# regras de uma marca (ou de todas), da mais nova para a mais antiga; na mesma data, a criada por último primeiro
def listar_regras(db: Session, marca: str | None = None) -> list[RegraFidelidade]:
    consulta = select(RegraFidelidade).order_by(
        RegraFidelidade.marca, RegraFidelidade.valida_desde.desc(), RegraFidelidade.id_regra.desc()
    )
    if marca is not None:
        consulta = consulta.where(RegraFidelidade.marca == marca)
    return list(db.scalars(consulta))


# passivo de pontos por marca: o que sobra em cada lote, valendo o que valia o ponto quando foi ganho (K3).
# O saldo negativo herdado não abate a dívida (K5f): ele não tem lote
def consulta_passivo() -> Select:
    return (
        select(
            LotePontos.marca,
            func.coalesce(func.sum(LotePontos.restante), 0).label("pontos"),
            func.coalesce(func.sum(LotePontos.restante * LotePontos.valor_ponto), 0).label("reais"),
        )
        .where(LotePontos.restante > 0)
        .group_by(LotePontos.marca)
        .order_by(LotePontos.marca)
    )


def passivo(db: Session) -> list:
    return list(db.execute(consulta_passivo()).mappings().all())


# regra que vale hoje para a marca (a de data mais recente até hoje; na mesma data, a criada por último)
def regra_vigente(db: Session, marca: str, hoje) -> RegraFidelidade | None:
    return db.scalar(
        select(RegraFidelidade)
        .where(RegraFidelidade.marca == marca, RegraFidelidade.valida_desde <= hoje)
        .order_by(RegraFidelidade.valida_desde.desc(), RegraFidelidade.id_regra.desc())
        .limit(1)
    )


def _do_dono(tabela, id_cliente, id_cliente_sem_conta, marca: str):
    dono = tabela.id_cliente == id_cliente if id_cliente is not None \
        else tabela.id_cliente_sem_conta == id_cliente_sem_conta
    return and_(dono, tabela.marca == marca)


def saldo(db: Session, id_cliente, id_cliente_sem_conta, marca: str) -> int:
    valor = db.scalar(select(SaldoPontos.saldo).where(_do_dono(SaldoPontos, id_cliente, id_cliente_sem_conta, marca)))
    return valor or 0


# quanto valem os pontos num resgate agora, sem gastar: os lotes mais antigos primeiro (FIFO), cada ponto pelo
# valor da época (K3), como o trigger aplica_movimento_pontos vai gastar
def valor_do_resgate(db: Session, id_cliente, id_cliente_sem_conta, marca: str, pontos: int) -> Decimal:
    acumulado = func.sum(LotePontos.restante).over(order_by=(LotePontos.ganho_em, LotePontos.id_movimento))
    lotes = (
        select(LotePontos.restante, LotePontos.valor_ponto, acumulado.label("acumulado"))
        .where(_do_dono(LotePontos, id_cliente, id_cliente_sem_conta, marca), LotePontos.restante > 0)
        .subquery()
    )
    usado = func.least(lotes.c.restante, func.greatest(0, pontos - (lotes.c.acumulado - lotes.c.restante)))
    return db.scalar(select(func.coalesce(func.sum(usado * lotes.c.valor_ponto), 0)))


# quanto o resgate gastou de fato, pelo que o trigger tirou de cada lote
def valor_gasto(db: Session, id_movimento: int) -> Decimal:
    return db.scalar(
        select(func.coalesce(func.sum(ConsumoPontos.pontos * LotePontos.valor_ponto), 0))
        .join(LotePontos, LotePontos.id_movimento == ConsumoPontos.id_lote)
        .where(ConsumoPontos.id_movimento == id_movimento)
    )
