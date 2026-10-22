from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.vendas import Compra, Pedido
from src.repositories.pedido_repository import consulta_pedidos


# travar=True segura a linha até o fim da transação. A compra é sempre travada antes dos pedidos dela,
# na mesma ordem do pg_cron (cancela_vencidos): pagar, cancelar e vencer nunca se cruzam
def buscar_compra(db: Session, id_compra: int, travar: bool = False) -> Compra | None:
    consulta = select(Compra).where(Compra.id_compra == id_compra)
    if travar:
        consulta = consulta.with_for_update()
    return db.scalar(consulta.execution_options(populate_existing=True))


# os pedidos da compra, um por marca, sempre na ordem do id
def pedidos_da_compra(db: Session, id_compra: int, travar: bool = False) -> list[Pedido]:
    consulta = select(Pedido).where(Pedido.id_compra == id_compra).order_by(Pedido.id_pedido)
    if travar:
        consulta = consulta.with_for_update()
    return list(db.scalars(consulta.execution_options(populate_existing=True)))


# os pedidos da compra com o nome do cliente e da unidade, como na lista de pedidos
def pedidos_com_nomes(db: Session, id_compra: int) -> list:
    consulta = consulta_pedidos().where(Pedido.id_compra == id_compra).order_by(None).order_by(Pedido.id_pedido)
    return list(db.execute(consulta).all())
