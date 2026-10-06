from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.models.catalogo import CategoriaProduto


def listar_categorias(db: Session, ativo: bool | None = None) -> list[CategoriaProduto]:
    consulta = select(CategoriaProduto).order_by(CategoriaProduto.nome)
    if ativo is not None:
        consulta = consulta.where(CategoriaProduto.ativo == ativo)
    return list(db.scalars(consulta))


def buscar_categoria(db: Session, id_categoria: int) -> CategoriaProduto | None:
    return db.get(CategoriaProduto, id_categoria)


# comparação sem diferenciar maiúsculas: "Camisas" e "camisas" são a mesma categoria
def categoria_por_nome(db: Session, nome: str) -> CategoriaProduto | None:
    return db.scalar(select(CategoriaProduto).where(func.lower(CategoriaProduto.nome) == nome.lower()))
