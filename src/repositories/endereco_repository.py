import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.vendas import EnderecoCliente


def listar_enderecos(db: Session, id_cliente: uuid.UUID) -> list[EnderecoCliente]:
    consulta = (
        select(EnderecoCliente)
        .where(EnderecoCliente.id_cliente == id_cliente)
        .order_by(EnderecoCliente.id_endereco)
    )
    return list(db.scalars(consulta))


def buscar_endereco(db: Session, id_endereco: int) -> EnderecoCliente | None:
    return db.get(EnderecoCliente, id_endereco)
