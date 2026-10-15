from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.models.catalogo import Destaque


# na ordem do carrossel; só_ativos para a loja, todos para quem gerencia o catálogo
def listar_destaques(db: Session, so_ativos: bool) -> list[Destaque]:
    consulta = select(Destaque).order_by(Destaque.ordem, Destaque.id_destaque)
    if so_ativos:
        consulta = consulta.where(Destaque.ativo)
    return list(db.scalars(consulta))


def buscar_destaque(db: Session, id_destaque: int) -> Destaque | None:
    return db.get(Destaque, id_destaque)


def proxima_ordem(db: Session) -> int:
    return (db.scalar(select(func.max(Destaque.ordem))) or 0) + 1
