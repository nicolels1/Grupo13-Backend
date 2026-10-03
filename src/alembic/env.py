from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool
from sqlalchemy import text

from alembic import context
from alembic.autogenerate import rewriter
from alembic.operations import ops

from src.config.settings import DATABASE_URL_DIRECT
from src.database.base import Base
import src.models  # noqa: F401 — registra os models no Base.metadata

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# migrations usam a conexão direta do Supabase, lida do .env (não do alembic.ini)
if not DATABASE_URL_DIRECT:
    raise RuntimeError("DATABASE_URL_DIRECT não definida no .env")
# "%" precisa ser escapado porque o Config do Alembic usa interpolação do configparser
config.set_main_option("sqlalchemy.url", DATABASE_URL_DIRECT.replace("%", "%%"))

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# metadata dos models para o --autogenerate
# (cada model novo precisa ser importado em src/models/__init__.py)
target_metadata = Base.metadata


# Tabelas de schemas do Supabase (ex.: auth.users) podem ser declaradas nos models
# só para servir de alvo de FK; o --autogenerate não deve tentar criá-las nem apagá-las.
SCHEMAS_EXTERNOS = {"auth", "storage"}


def include_object(object, name, type_, reflected, compare_to):
    if type_ == "table" and object.schema in SCHEMAS_EXTERNOS:
        return False
    return True


# Regra do modelo de dados: RLS ligado em todas as tabelas.
# Toda tabela criada pelo --autogenerate já ganha o ENABLE ROW LEVEL SECURITY na migration.
gerar_com_rls = rewriter.Rewriter()


@gerar_com_rls.rewrites(ops.CreateTableOp)
def habilitar_rls(context, revision, op):
    tabela = f'"{op.schema}"."{op.table_name}"' if op.schema else f'"{op.table_name}"'
    return [op, ops.ExecuteSQLOp(f"ALTER TABLE {tabela} ENABLE ROW LEVEL SECURITY")]


# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        include_object=include_object,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            process_revision_directives=gerar_com_rls,
        )

        with context.begin_transaction():
            context.run_migrations()

            # a alembic_version fica no schema public, que o Supabase expõe pela API;
            # sem RLS ela fica acessível por fora do backend
            if connection.dialect.name == "postgresql":
                connection.execute(
                    text("ALTER TABLE IF EXISTS alembic_version ENABLE ROW LEVEL SECURITY")
                )


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
