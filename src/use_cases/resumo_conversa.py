import logging
from datetime import datetime
from typing import Callable, TypeVar

from sqlalchemy.orm import Session

from src.models.assistente import ConversaCanal, ResumoConversa
from src.repositories import assistente_repository as repo
from src.use_cases.erros import ErroNegocio, RecursoNaoEncontrado

logger = logging.getLogger(__name__)

# resumo anônimo de cada conversa do assistente do cliente, lido pelo assistente interno (métricas da
# diretoria). Não guarda número, cliente nem texto: só o canal, a hora cheia, se a pessoa se confirmou,
# os assuntos, se algo falhou e o 👍/👎. Fica ligado ao número só enquanto a conversa está aberta

Resultado = TypeVar("Resultado")


def hora_cheia(momento: datetime) -> datetime:
    return momento.replace(minute=0, second=0, microsecond=0)


# começo de uma conversa nova: o resumo anterior fica para trás, sem ligação com o número
def abrir(db: Session, conversa: ConversaCanal, momento: datetime) -> ResumoConversa:
    resumo = ResumoConversa(canal=conversa.canal, iniciada_em=hora_cheia(momento), autenticou=False,
                            assuntos=[], virou_chamado=False, ferramenta_falhou=False)
    db.add(resumo)
    db.flush()
    conversa.id_resumo_atual = resumo.id_resumo
    return resumo


def _atual(db: Session, conversa: ConversaCanal | None) -> ResumoConversa | None:
    if conversa is None or conversa.id_resumo_atual is None:
        return None
    return repo.buscar_resumo(db, conversa.id_resumo_atual)


def _anotar_assunto(resumo: ResumoConversa, assunto: str) -> None:
    if assunto not in resumo.assuntos:
        resumo.assuntos = [*resumo.assuntos, assunto]  # lista nova: o SQLAlchemy não vê o append


def marcar_autenticou(db: Session, conversa: ConversaCanal) -> None:
    resumo = _atual(db, conversa)
    if resumo is not None:
        resumo.autenticou = True


def _anotar(db: Session, canal: str | None, id_externo: str | None, assunto: str, falhou: bool) -> None:
    if not canal or not id_externo:
        return  # chamada sem a conversa (ex.: teste manual da rota): nada a anotar
    resumo = _atual(db, repo.buscar_conversa(db, canal, id_externo))
    if resumo is None:
        return
    _anotar_assunto(resumo, assunto)
    if falhou:
        resumo.ferramenta_falhou = True
    db.commit()


# roda a ferramenta e anota o assunto no resumo da conversa. Erro de regra (busca vazia, peça sem a cor,
# sem login) é resposta normal; só o erro inesperado (rota quebrada, banco fora) marca a falha
def com_assunto(db: Session, canal: str | None, id_externo: str | None, assunto: str,
                ferramenta: Callable[[], Resultado]) -> Resultado:
    try:
        resultado = ferramenta()
    except ErroNegocio:
        _anotar(db, canal, id_externo, assunto, falhou=False)
        raise
    except Exception:
        db.rollback()
        try:
            _anotar(db, canal, id_externo, assunto, falhou=True)
        except Exception:
            logger.exception("não deu para marcar a falha no resumo da conversa")
        raise
    _anotar(db, canal, id_externo, assunto, falhou=False)
    return resultado


# o que só o n8n sabe: o 👍/👎 do cliente e a mensagem fora do tema
def registrar_do_n8n(db: Session, canal: str, id_externo: str, avaliacao: str | None, fora_do_tema: bool) -> None:
    resumo = _atual(db, repo.buscar_conversa(db, canal, id_externo))
    if resumo is None:
        raise RecursoNaoEncontrado("Conversa não encontrada: registre a mensagem antes")
    if avaliacao is not None:
        resumo.avaliacao = avaliacao
    if fora_do_tema:
        _anotar_assunto(resumo, "fora_do_tema")
    db.commit()
