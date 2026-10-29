from datetime import datetime

from sqlalchemy import String, and_, func, select
from sqlalchemy.dialects.postgresql import array
from sqlalchemy.orm import Session

from src.models.assistente import ASSUNTOS, ResumoConversa
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


# ---------- métricas do assistente do cliente (lê o resumo anônimo da Nicole, só leitura) ----------

ASSUNTOS_DO_TEMA = [a for a in ASSUNTOS if a != "fora_do_tema"]


# o resumo das conversas que começaram em [desde, ate) e antes de "fechadas_antes" (as em andamento ainda não têm o
# resultado). Cada conversa com tema cai num grupo só, nesta ordem: erro do sistema, virou chamado, 👎, resolvida
def resumo_das_conversas(db: Session, desde: datetime, ate: datetime, fechadas_antes: datetime,
                         canal: str | None = None) -> dict:
    r = ResumoConversa
    filtros = [r.iniciada_em >= desde, r.iniciada_em < ate, r.iniciada_em < fechadas_antes]
    if canal is not None:
        filtros.append(r.canal == canal)
    tem_tema = r.assuntos.overlap(array(ASSUNTOS_DO_TEMA, type_=String(20)))
    erro = and_(tem_tema, r.ferramenta_falhou)
    chamado = and_(tem_tema, ~r.ferramenta_falhou, r.virou_chamado)
    negativa = and_(tem_tema, ~r.ferramenta_falhou, ~r.virou_chamado, r.avaliacao == "negativa")
    resolvida = and_(tem_tema, ~r.ferramenta_falhou, ~r.virou_chamado, r.avaliacao.is_distinct_from("negativa"))
    contar = lambda condicao: func.count().filter(condicao)  # noqa: E731
    consulta = select(
        contar(tem_tema).label("conversas"),
        contar(resolvida).label("resolvidas"), contar(chamado).label("viraram_chamado"),
        contar(negativa).label("negativas"), contar(erro).label("erro_do_sistema"),
        contar(and_(tem_tema, r.autenticou)).label("se_identificaram"),
        contar(and_(tem_tema, r.avaliacao.is_not(None))).label("avaliadas"),
        contar(and_(tem_tema, r.avaliacao == "positiva")).label("positivas"),
        contar(and_(~tem_tema, r.assuntos.contains(["fora_do_tema"]))).label("so_fora_do_tema"),
        contar(func.cardinality(r.assuntos) == 0).label("sem_assunto"),
    ).where(*filtros)
    numeros = dict(db.execute(consulta).mappings().one())

    assunto = func.unnest(r.assuntos).label("assunto")
    por_assunto = select(assunto, func.count().label("conversas")).where(*filtros, tem_tema).group_by(assunto)
    numeros["por_assunto"] = sorted(({"assunto": a, "conversas": n} for a, n in db.execute(por_assunto)),
                                    key=lambda x: (-x["conversas"], x["assunto"]))

    # os canais que de fato tiveram conversa: a resposta diz só esses
    por_canal = select(r.canal, func.count()).where(*filtros, tem_tema).group_by(r.canal)
    numeros["por_canal"] = {c: n for c, n in db.execute(por_canal)}
    return numeros
