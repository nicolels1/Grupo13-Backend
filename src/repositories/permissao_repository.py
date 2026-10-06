import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.contas import (
    ModeloAcesso, ModeloPermissao, Permissao, Usuario, UsuarioPermissaoExcecao
)


def buscar_usuario(db: Session, id_usuario: uuid.UUID) -> Usuario | None:
    return db.get(Usuario, id_usuario)


def modelo_eh_admin(db: Session, id_modelo: int) -> bool:
    return bool(db.scalar(select(ModeloAcesso.eh_admin).where(ModeloAcesso.id_modelo == id_modelo)))


def codigos_do_modelo(db: Session, id_modelo: int) -> set[str]:
    consulta = (
        select(Permissao.codigo)
        .join(ModeloPermissao, ModeloPermissao.id_permissao == Permissao.id_permissao)
        .where(ModeloPermissao.id_modelo == id_modelo)
    )
    return set(db.scalars(consulta))


# devolve {codigo: efeito} das exceções da pessoa
def excecoes_do_usuario(db: Session, id_usuario: uuid.UUID) -> dict[str, str]:
    consulta = (
        select(Permissao.codigo, UsuarioPermissaoExcecao.efeito)
        .join(UsuarioPermissaoExcecao, UsuarioPermissaoExcecao.id_permissao == Permissao.id_permissao)
        .where(UsuarioPermissaoExcecao.id_usuario == id_usuario)
    )
    return {codigo: efeito for codigo, efeito in db.execute(consulta)}
