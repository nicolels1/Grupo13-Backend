from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.assistente_interno import ConversaInterna, MensagemInterna

MENSAGENS_DE_MEMORIA = 20


def buscar_conversa(db: Session, id_conversa: int) -> ConversaInterna | None:
    return db.get(ConversaInterna, id_conversa)


def criar_conversa(db: Session, id_usuario, agora: datetime) -> ConversaInterna:
    conversa = ConversaInterna(id_usuario=id_usuario, iniciada_em=agora, ultima_msg_em=agora)
    db.add(conversa)
    db.flush()
    return conversa


def adicionar_mensagem(db: Session, conversa: ConversaInterna, **campos) -> MensagemInterna:
    mensagem = MensagemInterna(id_conversa=conversa.id_conversa, **campos)
    db.add(mensagem)
    return mensagem


# a pergunta e a resposta de cada vez (sem as ferramentas), as últimas primeiro e depois na ordem
def memoria(db: Session, id_conversa: int) -> list[MensagemInterna]:
    consulta = (
        select(MensagemInterna)
        .where(MensagemInterna.id_conversa == id_conversa, MensagemInterna.papel.in_(("usuario", "assistente")),
               MensagemInterna.texto.is_not(None))
        .order_by(MensagemInterna.criado_em.desc(), MensagemInterna.id_mensagem.desc())
        .limit(MENSAGENS_DE_MEMORIA)
    )
    return list(reversed(db.scalars(consulta).all()))


def mensagens_da_conversa(db: Session, id_conversa: int) -> list[MensagemInterna]:
    consulta = (
        select(MensagemInterna)
        .where(MensagemInterna.id_conversa == id_conversa, MensagemInterna.papel.in_(("usuario", "assistente")))
        .order_by(MensagemInterna.criado_em, MensagemInterna.id_mensagem)
    )
    return list(db.scalars(consulta).all())
