from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from src.models.estoque import ItemTransferencia, Transferencia


# travar=True segura a linha até o fim da transação: duas pessoas não enviam a mesma transferência
def buscar_transferencia(db: Session, id_transferencia: int, travar: bool = False) -> Transferencia | None:
    consulta = select(Transferencia).where(Transferencia.id_transferencia == id_transferencia)
    if travar:
        consulta = consulta.with_for_update()
    return db.scalar(consulta.execution_options(populate_existing=True))


def itens_da_transferencia(db: Session, id_transferencia: int) -> list[ItemTransferencia]:
    consulta = (
        select(ItemTransferencia)
        .where(ItemTransferencia.id_transferencia == id_transferencia)
        .order_by(ItemTransferencia.id_item_transferencia)
    )
    return list(db.scalars(consulta))


def itens_das_transferencias(db: Session, ids: list[int]) -> dict[int, list[ItemTransferencia]]:
    agrupados: dict[int, list[ItemTransferencia]] = {i: [] for i in ids}
    if ids:
        consulta = (
            select(ItemTransferencia)
            .where(ItemTransferencia.id_transferencia.in_(ids))
            .order_by(ItemTransferencia.id_item_transferencia)
        )
        for item in db.scalars(consulta):
            agrupados[item.id_transferencia].append(item)
    return agrupados


# id_unidade filtra quem envia ou recebe; devolve uma página e o total
def listar_transferencias(db: Session, status: str | None, id_unidade: int | None, limit: int,
                          offset: int) -> tuple[list[Transferencia], int]:
    consulta = select(Transferencia)
    if status is not None:
        consulta = consulta.where(Transferencia.status == status)
    if id_unidade is not None:
        consulta = consulta.where(or_(Transferencia.id_unidade_origem == id_unidade,
                                      Transferencia.id_unidade_destino == id_unidade))
    total = db.scalar(select(func.count()).select_from(consulta.subquery()))
    pagina = (
        consulta.order_by(Transferencia.solicitada_em.desc(), Transferencia.id_transferencia.desc())
        .limit(limit).offset(offset)
    )
    return list(db.scalars(pagina)), total
