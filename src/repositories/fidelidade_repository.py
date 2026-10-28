from datetime import datetime
from decimal import Decimal

from sqlalchemy import Select, String, and_, func, or_, select
from sqlalchemy.orm import Session

from src.models.contas import ClienteSemConta
from src.models.fidelidade import ConsumoPontos, LotePontos, MovimentoPontos, RegraFidelidade, SaldoPontos
from src.models.vendas import Pedido


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


# ---------- saldo e extrato ----------

# dono dos movimentos: a conta e os clientes sem conta que passaram para ela (o extrato guarda o dono original)
def _movimentos_do_dono(id_cliente, id_cliente_sem_conta):
    if id_cliente is None:
        return MovimentoPontos.id_cliente_sem_conta == id_cliente_sem_conta
    passaram = select(ClienteSemConta.id_cliente_sem_conta).where(ClienteSemConta.id_usuario == id_cliente)
    return or_(MovimentoPontos.id_cliente == id_cliente, MovimentoPontos.id_cliente_sem_conta.in_(passaram))


# saldo de cada marca do cliente, com quanto valem os pontos (K3) e o próximo vencimento
def resumo(db: Session, id_cliente, id_cliente_sem_conta) -> list:
    do_dono = SaldoPontos.id_cliente == id_cliente if id_cliente is not None \
        else SaldoPontos.id_cliente_sem_conta == id_cliente_sem_conta
    lotes = select(LotePontos).where(
        LotePontos.marca == SaldoPontos.marca, LotePontos.restante > 0,
        LotePontos.id_cliente.is_not_distinct_from(SaldoPontos.id_cliente),
        LotePontos.id_cliente_sem_conta.is_not_distinct_from(SaldoPontos.id_cliente_sem_conta),
    ).correlate(SaldoPontos).subquery()
    reais = select(func.coalesce(func.sum(lotes.c.restante * lotes.c.valor_ponto), 0)).scalar_subquery()
    proximo = select(func.min(lotes.c.vence_em)).scalar_subquery()
    a_vencer = select(func.coalesce(func.sum(lotes.c.restante), 0)).where(
        lotes.c.vence_em == select(func.min(lotes.c.vence_em)).scalar_subquery()
    ).scalar_subquery()
    consulta = (
        select(*SaldoPontos.__table__.columns, reais.label("valor_em_reais"), proximo.label("proximo_vencimento"),
               a_vencer.label("pontos_no_proximo_vencimento"))
        .where(do_dono)
        .order_by(SaldoPontos.marca)
    )
    return list(db.execute(consulta).mappings().all())


# extrato do mais novo para o mais antigo; nas entradas, o que sobra e quando vence
def extrato(db: Session, id_cliente, id_cliente_sem_conta, marca: str | None, limit: int, offset: int):
    filtros = [_movimentos_do_dono(id_cliente, id_cliente_sem_conta)]
    if marca is not None:
        filtros.append(MovimentoPontos.marca == marca)
    consulta = (
        select(*MovimentoPontos.__table__.columns, Pedido.codigo_venda, LotePontos.restante, LotePontos.vence_em)
        .outerjoin(Pedido, Pedido.id_pedido == MovimentoPontos.id_pedido)
        .outerjoin(LotePontos, LotePontos.id_movimento == MovimentoPontos.id_movimento)
        .where(*filtros)
    )
    total = db.scalar(select(func.count()).select_from(consulta.subquery()))
    ordem = (MovimentoPontos.criado_em.desc(), MovimentoPontos.id_movimento.desc())
    linhas = db.execute(consulta.order_by(*ordem).limit(limit).offset(offset)).mappings().all()
    return list(linhas), total


# passivo de cada marca separado pela época do ponto (o valor que ele tinha quando foi ganho, K3). Num momento
# passado: os lotes lançados até ali, com o que sobra hoje mais o que saiu deles depois (resgate, vencimento e
# devolução de resgate). Partir do que sobra hoje, e não dos pontos do lote, desconta o que o lote pagou do
# saldo negativo herdado ao nascer (K5f), que não vira consumo
def passivo_por_valor(db: Session, em: datetime | None = None) -> list:
    restante, filtros = LotePontos.restante, [LotePontos.restante > 0]
    if em is not None:
        saiu_depois = (select(func.coalesce(func.sum(ConsumoPontos.pontos), 0))
                       .join(MovimentoPontos, MovimentoPontos.id_movimento == ConsumoPontos.id_movimento)
                       .where(ConsumoPontos.id_lote == LotePontos.id_movimento, MovimentoPontos.criado_em > em)
                       .correlate(LotePontos)
                       .scalar_subquery())
        restante = LotePontos.restante + saiu_depois
        filtros = [LotePontos.id_movimento.in_(
            select(MovimentoPontos.id_movimento).where(MovimentoPontos.criado_em <= em))]
    pontos = func.sum(restante)
    return list(db.execute(
        select(LotePontos.marca, LotePontos.valor_ponto, pontos.label("pontos"))
        .where(*filtros)
        .group_by(LotePontos.marca, LotePontos.valor_ponto)
        .having(pontos > 0)
        .order_by(LotePontos.marca, LotePontos.valor_ponto.desc())
    ).mappings().all())


# ---------- pontos vencendo (assistente interno) ----------

FUSO = "America/Sao_Paulo"

# o que sobra nos lotes com vencimento no intervalo (desde, ate], por marca: quantos clientes, quantos pontos e
# quanto valem (cada ponto pelo valor da época em que foi ganho), o primeiro vencimento e de quando são (o lote
# mais antigo e o mais recente). Cliente = a conta ou,
# sem ela, o cliente da Vulto que ainda não criou conta (P2)
def pontos_vencendo(db: Session, desde: datetime, ate: datetime | None, marca: str | None = None) -> list:
    lote = LotePontos
    dono = func.coalesce(func.cast(lote.id_cliente, String), "sem_conta_" + func.cast(lote.id_cliente_sem_conta, String))
    consulta = (
        select(lote.marca, func.count(func.distinct(dono)).label("clientes"), func.sum(lote.restante).label("pontos"),
               func.sum(lote.restante * lote.valor_ponto).label("reais"), func.min(lote.vence_em).label("primeiro"),
               func.min(lote.ganho_em).label("ganho_desde"), func.max(lote.ganho_em).label("ganho_ate"))
        .where(lote.restante > 0, lote.vence_em > desde)
        .group_by(lote.marca)
        .order_by(lote.marca)
    )
    if ate is not None:
        consulta = consulta.where(lote.vence_em <= ate)
    if marca is not None:
        consulta = consulta.where(lote.marca == marca)
    return list(db.execute(consulta).mappings().all())


# o próximo dia (no horário de Brasília) com pontos vencendo depois de "desde", por marca, com o que vence nele
def proximo_vencimento(db: Session, desde: datetime, marca: str | None = None) -> list:
    lote = LotePontos
    dia = func.date(func.timezone(FUSO, lote.vence_em))
    primeiro = (
        select(lote.marca, func.min(dia).label("dia"))
        .where(lote.restante > 0, lote.vence_em > desde)
        .group_by(lote.marca)
    )
    if marca is not None:
        primeiro = primeiro.where(lote.marca == marca)
    primeiro = primeiro.subquery()
    consulta = (
        select(lote.marca, primeiro.c.dia, func.sum(lote.restante).label("pontos"),
               func.min(lote.ganho_em).label("ganho_desde"), func.max(lote.ganho_em).label("ganho_ate"))
        .join(primeiro, and_(primeiro.c.marca == lote.marca, primeiro.c.dia == dia))
        .where(lote.restante > 0, lote.vence_em > desde)
        .group_by(lote.marca, primeiro.c.dia)
        .order_by(lote.marca)
    )
    return list(db.execute(consulta).mappings().all())
