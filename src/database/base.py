from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# nomes padronizados para PK, FK, UNIQUE etc., assim o Alembic gera migrations previsíveis
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",  # todas as colunas, para UNIQUE composto
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


# todos os models em src/models herdam desta classe
class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
