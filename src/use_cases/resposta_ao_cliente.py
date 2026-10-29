import logging

from sqlalchemy.orm import Session

from src.config import settings
from src.models.atendimento import Chamado
from src.models.contas import Usuario
from src.repositories import assistente_repository
from src.utils.telegram import TAMANHO_MAXIMO, ErroTelegram, enviar_telegram

logger = logging.getLogger(__name__)

# o chamado aberto pelo assistente do cliente continua na conversa do canal: a resposta da equipe e o aviso
# de concluído chegam lá, com o nome de quem atendeu na frente, para o cliente saber o que é da pessoa e o
# que é do assistente. Falha no envio não desfaz nada: a mensagem já está no chamado e o erro fica no log

def _link_do_chamado(chamado: Chamado) -> str:
    return f"{settings.SITE_URL}/loja/chamados/{chamado.id_chamado}"


def _cortar(texto: str, chamado: Chamado) -> str:
    if len(texto) <= TAMANHO_MAXIMO:
        return texto
    continua = f"… (continua no site: {_link_do_chamado(chamado)})"
    return texto[:TAMANHO_MAXIMO - len(continua)] + continua


def _enviar(db: Session, chamado: Chamado, texto: str) -> None:
    # chamado do site ou migrado da Vulto não tem conversa ligada: o cliente vê a resposta no site
    if not chamado.aberto_pelo_assistente:
        return
    conversa = assistente_repository.conversa_do_chamado(db, chamado.id_chamado)
    if conversa is None:
        return
    if conversa.canal != "telegram":
        # o envio pela API da Meta entra quando o WhatsApp for liberado
        logger.warning("chamado %s: envio pelo %s ainda não existe", chamado.id_chamado, conversa.canal)
        return
    try:
        enviar_telegram(conversa.id_externo, _cortar(texto, chamado))
    except ErroTelegram:
        logger.error("chamado %s: resposta não chegou ao cliente pelo Telegram", chamado.id_chamado)


def enviar_resposta_ao_cliente(db: Session, chamado: Chamado, autor: Usuario, conteudo: str | None,
                               anexo_nome: str | None = None) -> None:
    partes = [f"👤 {autor.nome.split()[0]}, do atendimento:"]
    if conteudo:
        partes.append(conteudo)
    if anexo_nome:
        partes.append(f"📎 Mandou o arquivo {anexo_nome}. Para abrir: {_link_do_chamado(chamado)}")
    _enviar(db, chamado, "\n".join(partes))


# a conversa volta para o assistente na hora, sem esperar a próxima mensagem do cliente
def avisar_chamado_concluido(db: Session, chamado: Chamado) -> None:
    if not chamado.aberto_pelo_assistente:
        return
    conversa = assistente_repository.conversa_do_chamado(db, chamado.id_chamado)
    if conversa is None:
        return
    _enviar(db, chamado, f"✅ Seu chamado #{chamado.id_chamado} foi concluído pela equipe. Se precisar de mais "
                         "alguma coisa, é só escrever por aqui.")
    conversa.id_chamado = None
    if conversa.etapa == "em_chamado":
        conversa.etapa = "livre"
    db.commit()
