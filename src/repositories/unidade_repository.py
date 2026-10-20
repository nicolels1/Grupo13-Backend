from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from src.models.estoque import MovimentacaoEstoque, Unidade
from src.models.vendas import Pedido


def listar_unidades(db: Session, ativo: bool | None = None, tipo: str | None = None) -> list[Unidade]:
    consulta = select(Unidade).order_by(Unidade.nome)
    if ativo is not None:
        consulta = consulta.where(Unidade.ativo == ativo)
    if tipo is not None:
        consulta = consulta.where(Unidade.tipo == tipo)
    return list(db.scalars(consulta))


def buscar_unidade(db: Session, id_unidade: int) -> Unidade | None:
    return db.get(Unidade, id_unidade)


def unidade_por_nome(db: Session, nome: str) -> Unidade | None:
    return db.scalar(select(Unidade).where(func.lower(Unidade.nome) == nome.lower()))


# já teve movimentação de estoque ou venda: a partir daí a marca faz parte do histórico
def tem_historico(db: Session, id_unidade: int) -> bool:
    return db.scalar(select(or_(
        exists().where(MovimentacaoEstoque.id_unidade == id_unidade),
        exists().where(Pedido.id_unidade == id_unidade),
    )))
