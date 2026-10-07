from sqlalchemy.orm import Session

from src.models.contas import Usuario
from src.models.vendas import EnderecoCliente
from src.repositories import endereco_repository as repo
from src.use_cases.erros import RecursoNaoEncontrado


def listar(db: Session, cliente: Usuario) -> list[EnderecoCliente]:
    return repo.listar_enderecos(db, cliente.id_usuario)


# endereço de outra pessoa responde como inexistente: trocar o número na URL não revela nada
def do_cliente(db: Session, cliente: Usuario, id_endereco: int) -> EnderecoCliente:
    endereco = repo.buscar_endereco(db, id_endereco)
    if endereco is None or endereco.id_cliente != cliente.id_usuario:
        raise RecursoNaoEncontrado("Endereço não encontrado")
    return endereco


def criar(db: Session, cliente: Usuario, dados: dict) -> EnderecoCliente:
    endereco = EnderecoCliente(id_cliente=cliente.id_usuario, **dados)
    db.add(endereco)
    db.commit()
    db.refresh(endereco)
    return endereco


def alterar(db: Session, cliente: Usuario, id_endereco: int, campos: dict) -> EnderecoCliente:
    endereco = do_cliente(db, cliente, id_endereco)
    for campo, valor in campos.items():
        setattr(endereco, campo, valor)
    db.commit()
    db.refresh(endereco)
    return endereco


# o único dado que o cliente apaga: o pedido guarda a própria cópia do endereço (case, seção 5)
def apagar(db: Session, cliente: Usuario, id_endereco: int) -> None:
    db.delete(do_cliente(db, cliente, id_endereco))
    db.commit()
