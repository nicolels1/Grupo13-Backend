from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from src.models.contas import ModeloAcesso, ModeloPermissao, Permissao, Usuario


def listar_permissoes(db: Session) -> list[Permissao]:
    return list(db.scalars(select(Permissao).order_by(Permissao.codigo)))


# {codigo: id_permissao} dos códigos que existem
def ids_das_permissoes(db: Session, codigos: list[str]) -> dict[str, int]:
    consulta = select(Permissao.codigo, Permissao.id_permissao).where(Permissao.codigo.in_(codigos))
    return dict(db.execute(consulta).tuples().all())


def listar_modelos(db: Session) -> list[ModeloAcesso]:
    return list(db.scalars(select(ModeloAcesso).order_by(ModeloAcesso.eh_admin.desc(), ModeloAcesso.nome)))


def buscar_modelo(db: Session, id_modelo: int) -> ModeloAcesso | None:
    return db.get(ModeloAcesso, id_modelo)


def modelo_por_nome(db: Session, nome: str) -> ModeloAcesso | None:
    return db.scalar(select(ModeloAcesso).where(func.lower(ModeloAcesso.nome) == nome.lower()))


# {id_modelo: [códigos]} de todos os modelos numa consulta
def codigos_por_modelo(db: Session) -> dict[int, list[str]]:
    consulta = (
        select(ModeloPermissao.id_modelo, Permissao.codigo)
        .join(Permissao, Permissao.id_permissao == ModeloPermissao.id_permissao)
        .order_by(Permissao.codigo)
    )
    agrupados: dict[int, list[str]] = {}
    for id_modelo, codigo in db.execute(consulta):
        agrupados.setdefault(id_modelo, []).append(codigo)
    return agrupados


# {id_modelo: quantidade de pessoas ligadas}
def pessoas_por_modelo(db: Session) -> dict[int, int]:
    consulta = (
        select(Usuario.id_modelo_acesso, func.count())
        .where(Usuario.id_modelo_acesso.is_not(None))
        .group_by(Usuario.id_modelo_acesso)
    )
    return dict(db.execute(consulta).tuples().all())


def trocar_permissoes(db: Session, id_modelo: int, ids_permissao: list[int]) -> None:
    db.execute(delete(ModeloPermissao).where(ModeloPermissao.id_modelo == id_modelo))
    db.add_all(ModeloPermissao(id_modelo=id_modelo, id_permissao=i) for i in ids_permissao)
