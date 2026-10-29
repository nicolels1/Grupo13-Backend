import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from src.config import settings
from src.models.assistente import ConversaCanal
from src.models.atendimento import Chamado
from src.models.contas import Usuario
from src.repositories import assistente_repository, permissao_repository
from src.utils import telegram, whatsapp
from src.utils.email import ErroEmail, enviar_email
from src.utils.telegram import ErroTelegram, enviar_telegram
from src.utils.whatsapp import JANELA_DE_ATENDIMENTO, ErroWhatsapp, enviar_whatsapp

logger = logging.getLogger(__name__)

# o chamado aberto pelo assistente do cliente continua na conversa do canal: a resposta da equipe e o aviso
# de concluído chegam lá, com o nome de quem atendeu na frente, para o cliente saber o que é da pessoa e o
# que é do assistente. Falha no envio não desfaz nada: a mensagem já está no chamado e o erro fica no log.
# No WhatsApp, fora da janela de 24h da Meta (ou se a Meta recusar), o cliente recebe o aviso por e-mail

def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _link_do_chamado(chamado: Chamado) -> str:
    return f"{settings.SITE_URL}/loja/chamados/{chamado.id_chamado}"


def _cortar(texto: str, chamado: Chamado, tamanho_maximo: int) -> str:
    if len(texto) <= tamanho_maximo:
        return texto
    continua = f"… (continua no site: {_link_do_chamado(chamado)})"
    return texto[:tamanho_maximo - len(continua)] + continua


def _janela_aberta(conversa: ConversaCanal) -> bool:
    ultima = conversa.ultima_msg_cliente_em
    return ultima is not None and _agora() - ultima < JANELA_DE_ATENDIMENTO


def _avisar_por_email(db: Session, chamado: Chamado, assunto: str, texto: str) -> None:
    cliente = permissao_repository.buscar_usuario(db, chamado.id_cliente) if chamado.id_cliente else None
    if cliente is None:
        logger.error("chamado %s: sem cliente para avisar por e-mail", chamado.id_chamado)
        return
    try:
        enviar_email(cliente.email, assunto, f"{texto}\n\nPara ver o chamado: {_link_do_chamado(chamado)}\n")
    except ErroEmail:
        logger.error("chamado %s: aviso por e-mail não enviado", chamado.id_chamado)


def _pelo_whatsapp(db: Session, chamado: Chamado, conversa: ConversaCanal, assunto: str, texto: str,
                   texto_do_email: str) -> None:
    if not _janela_aberta(conversa):
        logger.info("chamado %s: fora da janela de 24h do WhatsApp, aviso por e-mail", chamado.id_chamado)
        _avisar_por_email(db, chamado, assunto, texto_do_email)
        return
    try:
        enviar_whatsapp(conversa.id_externo, _cortar(texto, chamado, whatsapp.TAMANHO_MAXIMO))
    except ErroWhatsapp:
        logger.error("chamado %s: resposta não chegou pelo WhatsApp, aviso por e-mail", chamado.id_chamado)
        _avisar_por_email(db, chamado, assunto, texto_do_email)


# texto_do_email: quando a mensagem da conversa não serve no e-mail (o "escrever por aqui" do concluído)
def _enviar(db: Session, chamado: Chamado, assunto: str, texto: str, texto_do_email: str | None = None) -> None:
    # chamado do site ou migrado da Vulto não tem conversa ligada: o cliente vê a resposta no site
    if not chamado.aberto_pelo_assistente:
        return
    conversa = assistente_repository.conversa_do_chamado(db, chamado.id_chamado)
    if conversa is None:
        return
    if conversa.canal == "whatsapp":
        _pelo_whatsapp(db, chamado, conversa, assunto, texto, texto_do_email or texto)
        return
    try:
        enviar_telegram(conversa.id_externo, _cortar(texto, chamado, telegram.TAMANHO_MAXIMO))
    except ErroTelegram:
        logger.error("chamado %s: resposta não chegou ao cliente pelo Telegram", chamado.id_chamado)


def enviar_resposta_ao_cliente(db: Session, chamado: Chamado, autor: Usuario, conteudo: str | None,
                               anexo_nome: str | None = None) -> None:
    partes = [f"👤 {autor.nome.split()[0]}, do atendimento:"]
    if conteudo:
        partes.append(conteudo)
    if anexo_nome:
        partes.append(f"📎 Mandou o arquivo {anexo_nome}. Para abrir: {_link_do_chamado(chamado)}")
    _enviar(db, chamado, f"Resposta no seu chamado #{chamado.id_chamado}", "\n".join(partes))


# a conversa volta para o assistente na hora, sem esperar a próxima mensagem do cliente
def avisar_chamado_concluido(db: Session, chamado: Chamado) -> None:
    if not chamado.aberto_pelo_assistente:
        return
    conversa = assistente_repository.conversa_do_chamado(db, chamado.id_chamado)
    if conversa is None:
        return
    concluido = f"✅ Seu chamado #{chamado.id_chamado} foi concluído pela equipe. Se precisar de mais alguma coisa, "
    _enviar(db, chamado, f"Seu chamado #{chamado.id_chamado} foi concluído", concluido + "é só escrever por aqui.",
            concluido + "é só chamar a gente no WhatsApp.")
    conversa.id_chamado = None
    if conversa.etapa == "em_chamado":
        conversa.etapa = "livre"
    db.commit()
