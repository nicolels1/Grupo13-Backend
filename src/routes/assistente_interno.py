from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from src.database.session import get_db
from src.dependencies.permissoes import get_usuario_ativo
from src.entities.assistente_interno import AvaliacaoEntrada, ConversaSaida, PerguntaEntrada, RespostaAssistente
from src.models.contas import Usuario
from src.use_cases import assistente_interno

# assistente interno: a equipe pergunta pela plataforma, com o próprio login. Cada ferramenta confere a
# permissão e a marca da pessoa, como as outras rotas
router = APIRouter(
    prefix="/assistente/interno",
    tags=["assistente interno"],
    responses={401: {"description": "Sem login válido"}, 403: {"description": "Só para contas internas"}},
)


@router.post(
    "/mensagens",
    response_model=RespostaAssistente,
    responses={404: {"description": "Conversa não encontrada"}, 503: {"description": "Assistente fora do ar"}},
    description="Uma pergunta ao assistente. Os números vêm das ferramentas do backend e passam pela conferência "
                "dos números antes de voltar.",
)
def perguntar(
    dados: PerguntaEntrada,
    usuario: Usuario = Depends(get_usuario_ativo),
    db: Session = Depends(get_db),
):
    return assistente_interno.responder(db, usuario, dados.texto, dados.id_conversa, dados.id_unidade_em_foco)


@router.get(
    "/conversas/{id_conversa}",
    response_model=ConversaSaida,
    responses={404: {"description": "Conversa não encontrada"}},
    description="Perguntas e respostas de uma conversa: a própria pessoa, ou o Admin para revisar.",
)
def ver_conversa(id_conversa: int, usuario: Usuario = Depends(get_usuario_ativo), db: Session = Depends(get_db)):
    return assistente_interno.ver_conversa(db, usuario, id_conversa)


@router.put(
    "/conversas/{id_conversa}/avaliacao",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"description": "Conversa não encontrada"}},
    description="👍 ou 👎 da pessoa ao fim da conversa (opcional).",
)
def avaliar(
    id_conversa: int,
    dados: AvaliacaoEntrada,
    usuario: Usuario = Depends(get_usuario_ativo),
    db: Session = Depends(get_db),
):
    assistente_interno.avaliar(db, usuario, id_conversa, dados.avaliacao)
