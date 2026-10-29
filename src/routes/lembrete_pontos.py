from html import escape

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from src.database.session import get_db
from src.use_cases import assistente_conta

# link "parar de receber" do e-mail de pontos vencendo: o cliente abre no navegador, sem login e sem a chave do
# assistente; quem prova a conta é a assinatura do link. Abrir o link só mostra o botão: quem desliga é o POST,
# para o leitor de links do e-mail (antivírus, pré-visualização) não desligar sem o cliente pedir
router = APIRouter(prefix="/lembrete-pontos", tags=["lembrete de pontos"])


def _pagina(titulo: str, corpo: str, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(status_code=status_code, content=(
        '<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{titulo} · Casa Lorenzi</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:28rem;margin:4rem auto;padding:0 1rem;"
        "color:#1c1917;line-height:1.5}button{font:inherit;padding:.6rem 1rem;border:0;border-radius:.4rem;"
        "background:#1c1917;color:#fff;cursor:pointer}</style>"
        f"</head><body><h1>{titulo}</h1>{corpo}</body></html>"
    ))


@router.get("/parar", response_class=HTMLResponse, description="Página com o botão para parar o lembrete.")
def confirmar_parada(token: str = Query(max_length=200)):
    return _pagina(
        "Parar o lembrete de pontos",
        "<p>Você deixa de receber o e-mail que avisa antes de os seus pontos vencerem.</p>"
        f'<form method="post" action="/lembrete-pontos/parar?token={escape(token, quote=True)}">'
        "<button>Parar de receber</button></form>",
    )


@router.post("/parar", response_class=HTMLResponse, description="Desliga o lembrete da conta do link.")
def parar(token: str = Query(max_length=200), db: Session = Depends(get_db)):
    if not assistente_conta.parar_lembrete_pelo_link(db, token):
        return _pagina("Link inválido", "<p>Este link não é válido. Confira se ele veio inteiro do e-mail.</p>", 400)
    return _pagina(
        "Pronto",
        "<p>Você não vai mais receber o lembrete de pontos. Para voltar a receber, é só pedir no atendimento.</p>",
    )
