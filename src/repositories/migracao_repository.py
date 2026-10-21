from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from src.models.migracao import OrigemMigracao


# de onde veio o registro e o que a migração mudou nele, na ordem em que a carga gravou
def origem(db: Session, tabela: str, id_registro: str) -> list[OrigemMigracao]:
    consulta = (
        select(OrigemMigracao)
        .where(OrigemMigracao.tabela == tabela, OrigemMigracao.id_registro == id_registro)
        .order_by(OrigemMigracao.id_origem)
    )
    return list(db.scalars(consulta))


# o registro veio da migração da Vulto quando tem linha na origem da migração (P4)
def migrado(db: Session, tabela: str, id_registro: str) -> bool:
    return db.scalar(select(exists().where(
        OrigemMigracao.tabela == tabela, OrigemMigracao.id_registro == id_registro,
    )))
