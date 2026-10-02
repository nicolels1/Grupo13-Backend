from fastapi import FastAPI, Depends
from src.middlewares.auth import get_current_user

app = FastAPI()

#primeira rota pra testar
@app.get("/")
def home():
    return {"status": "ok"}

@app.get("/protegida")
def rota_protegida(user_id: str = Depends(get_current_user)):
    return {"mensagem": "Você está autenticado!", "user_id": user_id}