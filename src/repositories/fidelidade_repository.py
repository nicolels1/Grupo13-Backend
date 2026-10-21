from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from src.models.fidelidade import LotePontos, RegraFidelidade


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
