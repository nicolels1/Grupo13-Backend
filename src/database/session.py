from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.config.settings import DATABASE_URL

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL não definida no .env")

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,  # descarta conexões que o pooler já fechou
    connect_args={"prepare_threshold": None},  # o transaction pooler não suporta prepared statements
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


# Dependência do FastAPI: abre uma sessão por requisição e fecha no final
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
