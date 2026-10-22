from sqlalchemy.orm import Session

from src.models.migracao import OrigemMigracao
from src.repositories import migracao_repository as repo


# de onde veio o registro migrado da Vulto e o que a migração mudou nele (P4); vazio: não veio da migração
def origem_do_registro(db: Session, tabela: str, id_registro: str) -> list[OrigemMigracao]:
    return repo.origem(db, tabela, id_registro)
