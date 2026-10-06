from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.database.session import get_db

router = APIRouter(tags=["health"])


# usada pelo serviço externo que mantém o Render e o banco acordados
@router.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="Banco indisponível")
    return {"status": "ok"}
