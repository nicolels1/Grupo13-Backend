from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from src.database.session import get_db
from src.middlewares.auth import get_current_user
from src.middlewares.cors import configurar_cors

app = FastAPI(title="Casa Lorenzi API")
configurar_cors(app)

# usada pelo serviço externo que mantém o Render e o banco acordados
@app.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="Banco indisponível")
    return {"status": "ok"}

#primeira rota pra testar
@app.get("/categorias")
def listar_categorias():
    return {'items': []}

@app.get("/produtos")
def listar_produtos():
    return {'items': []}

@app.get("/produtos/{id}/imagens")
def mostrar_imagens():
    return {'items': []}

@app.get("/unidades")
def listar_unidades():
    return {'items': []}

@app.get("/estoque")
def listar_estoque():
    return {'items': []}

@app.get("/movimentacoes-estoque")
def listar_movimentacoes(user_id: str = Depends(get_current_user)):
    return {'items': []}

@app.get("/transferencias")
def listar_transferencias(user_id: str = Depends(get_current_user)):
    return {'items': []}

@app.get("/pedidos")
def listar_pedidos(user_id: str = Depends(get_current_user)):
    return {'items': []}

@app.get("/enderecos")
def listar_enderecos(user_id: str = Depends(get_current_user)):
    return {'items': []}

@app.get("/pagamentos")
def listar_pagamentos(user_id: str = Depends(get_current_user)):
    return {'items': []}

@app.get("/chamados")
def listar_chamados(user_id: str = Depends(get_current_user)):
    return {'items': []}

@app.get("/chamados/{id}/mensagens")
def listar_mensagens(user_id: str = Depends(get_current_user)):
    return {'items': []}

@app.get("/avaliacoes")
def listar_avaliacoes():
    return {'items': []}

@app.get("/usuarios")
def listar_usuarios(user_id: str = Depends(get_current_user)):
    return {'items': []}