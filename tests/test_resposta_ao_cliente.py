import logging
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx2
import pytest

from src.config import settings
from src.models.assistente import ConversaCanal
from src.models.atendimento import Chamado
from src.repositories import assistente_repository, atendimento_repository, permissao_repository
from src.use_cases import atendimento, resposta_ao_cliente
from src.utils import telegram, whatsapp
from src.utils.email import ErroEmail
from src.utils.telegram import ErroTelegram, enviar_telegram
from src.utils.whatsapp import ErroWhatsapp, enviar_whatsapp
from tests.apoio import SessaoFalsa, funcionario

TOKEN = "123:segredo-do-bot"
CHAT_ID = "987654321"
TOKEN_META = "EAAG-segredo-da-meta"
ID_DO_NUMERO = "1098765432"
TELEFONE = "+5511999998888"
AGORA = datetime(2026, 10, 30, 15, 0, tzinfo=timezone.utc)
ID_CLIENTE = uuid.UUID("44444444-4444-4444-4444-444444444444")
JULIANA = funcionario(nome="Juliana Prado")


def chamado(canal_externo="telegram", status="em_andamento", aberto_pelo_assistente=True):
    return Chamado(id_chamado=12, id_cliente=ID_CLIENTE, status=status, categoria="duvida", assunto="Troca",
                   descricao="Quer trocar", canal_externo=canal_externo,
                   aberto_pelo_assistente=aberto_pelo_assistente)


@pytest.fixture
def requisicoes(monkeypatch):
    # troca a chamada HTTP por uma resposta pronta e guarda o que foi pedido
    estado = SimpleNamespace(enviadas=[], resposta=httpx2.Response(200, json={"ok": True}))

    def post(url, **kwargs):
        estado.enviadas.append((url, kwargs))
        if isinstance(estado.resposta, Exception):
            raise estado.resposta
        return estado.resposta

    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(settings, "WHATSAPP_TOKEN", TOKEN_META)
    monkeypatch.setattr(settings, "WHATSAPP_PHONE_NUMBER_ID", ID_DO_NUMERO)
    monkeypatch.setattr(telegram.httpx2, "post", post)
    return estado


@pytest.fixture
def conversa(monkeypatch):
    ligada = ConversaCanal(canal="telegram", id_externo=CHAT_ID, etapa="em_chamado", id_chamado=12)
    estado = SimpleNamespace(conversa=ligada, buscas=0)

    def buscar(db, id_chamado):
        estado.buscas += 1
        return estado.conversa if estado.conversa and estado.conversa.id_chamado == id_chamado else None

    monkeypatch.setattr(assistente_repository, "conversa_do_chamado", buscar)
    monkeypatch.setattr(settings, "SITE_URL", "https://loja.example")
    return estado


@pytest.fixture
def emails(monkeypatch):
    estado = SimpleNamespace(enviados=[], erro=None)

    def enviar(para, assunto, texto):
        if estado.erro:
            raise estado.erro
        estado.enviados.append((para, assunto, texto))

    cliente = SimpleNamespace(id_usuario=ID_CLIENTE, email="ana@example.com")
    monkeypatch.setattr(resposta_ao_cliente, "enviar_email", enviar)
    monkeypatch.setattr(permissao_repository, "buscar_usuario", lambda db, i: cliente if i == ID_CLIENTE else None)
    monkeypatch.setattr(resposta_ao_cliente, "_agora", lambda: AGORA)
    return estado


@pytest.fixture
def no_whatsapp(conversa):
    # o cliente escreveu há 2 horas: dentro da janela de 24h da Meta
    conversa.conversa.canal = "whatsapp"
    conversa.conversa.id_externo = TELEFONE
    conversa.conversa.ultima_msg_cliente_em = AGORA - timedelta(hours=2)
    return conversa


def textos(requisicoes) -> list[str]:
    return [kwargs["json"]["text"] for _, kwargs in requisicoes.enviadas]


def textos_do_whatsapp(requisicoes) -> list[str]:
    return [kwargs["json"]["text"]["body"] for _, kwargs in requisicoes.enviadas]


# ---------- envio pelo bot ----------

def test_envia_para_a_conversa_pelo_bot(requisicoes):
    enviar_telegram(CHAT_ID, "Olá")

    [(url, kwargs)] = requisicoes.enviadas
    assert url == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    assert kwargs["json"] == {"chat_id": CHAT_ID, "text": "Olá"}


def test_sem_token_nao_envia(requisicoes, monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", None)

    with pytest.raises(ErroTelegram):
        enviar_telegram(CHAT_ID, "Olá")
    assert requisicoes.enviadas == []


def test_telegram_fora_do_ar_nao_registra_o_token(requisicoes, caplog):
    requisicoes.resposta = httpx2.ConnectError(f"falhou em https://api.telegram.org/bot{TOKEN}/sendMessage")

    with caplog.at_level(logging.ERROR), pytest.raises(ErroTelegram) as erro:
        enviar_telegram(CHAT_ID, "Olá")

    assert TOKEN not in caplog.text
    assert erro.value.__cause__ is None


def test_telegram_recusa_a_mensagem(requisicoes):
    requisicoes.resposta = httpx2.Response(403, json={"ok": False, "description": "bot was blocked by the user"})

    with pytest.raises(ErroTelegram, match="403"):
        enviar_telegram(CHAT_ID, "Olá")


# ---------- envio pela API da Meta ----------

def test_envia_pelo_whatsapp_so_com_os_digitos_do_telefone(requisicoes):
    enviar_whatsapp(TELEFONE, "Olá")

    [(url, kwargs)] = requisicoes.enviadas
    assert url == f"https://graph.facebook.com/{whatsapp.VERSAO_API}/{ID_DO_NUMERO}/messages"
    assert kwargs["headers"] == {"Authorization": f"Bearer {TOKEN_META}"}
    assert kwargs["json"] == {"messaging_product": "whatsapp", "to": "5511999998888", "type": "text",
                              "text": {"body": "Olá", "preview_url": True}}


@pytest.mark.parametrize("variavel", ["WHATSAPP_TOKEN", "WHATSAPP_PHONE_NUMBER_ID"])
def test_whatsapp_sem_configuracao_nao_envia(requisicoes, monkeypatch, variavel):
    monkeypatch.setattr(settings, variavel, None)

    with pytest.raises(ErroWhatsapp):
        enviar_whatsapp(TELEFONE, "Olá")
    assert requisicoes.enviadas == []


def test_whatsapp_fora_do_ar_nao_registra_o_token(requisicoes, caplog):
    requisicoes.resposta = httpx2.ConnectError(f"falhou com Bearer {TOKEN_META}")

    with caplog.at_level(logging.ERROR), pytest.raises(ErroWhatsapp) as erro:
        enviar_whatsapp(TELEFONE, "Olá")

    assert TOKEN_META not in caplog.text
    assert erro.value.__cause__ is None


def test_whatsapp_recusa_a_mensagem(requisicoes):
    requisicoes.resposta = httpx2.Response(400, json={"error": {"code": 131047, "message": "Re-engagement"}})

    with pytest.raises(ErroWhatsapp, match="400"):
        enviar_whatsapp(TELEFONE, "Olá")


# ---------- resposta da equipe ----------

def test_resposta_chega_com_o_nome_de_quem_atendeu(requisicoes, conversa):
    resposta_ao_cliente.enviar_resposta_ao_cliente(SessaoFalsa(), chamado(), JULIANA, "Pode trocar na loja.")

    assert textos(requisicoes) == ["👤 Juliana, do atendimento:\nPode trocar na loja."]
    assert requisicoes.enviadas[0][1]["json"]["chat_id"] == CHAT_ID


def test_anexo_manda_o_link_do_chamado_no_site(requisicoes, conversa):
    resposta_ao_cliente.enviar_resposta_ao_cliente(SessaoFalsa(), chamado(), JULIANA, None, "etiqueta.pdf")

    assert textos(requisicoes) == ["👤 Juliana, do atendimento:\n📎 Mandou o arquivo etiqueta.pdf. Para abrir: "
                                   "https://loja.example/loja/chamados/12"]


def test_resposta_longa_e_cortada_com_o_link(requisicoes, conversa):
    resposta_ao_cliente.enviar_resposta_ao_cliente(SessaoFalsa(), chamado(), JULIANA, "a" * 5000)

    [texto] = textos(requisicoes)
    assert len(texto) == telegram.TAMANHO_MAXIMO
    assert texto.endswith("(continua no site: https://loja.example/loja/chamados/12)")


def test_chamado_do_site_nao_procura_conversa(requisicoes, conversa):
    resposta_ao_cliente.enviar_resposta_ao_cliente(
        SessaoFalsa(), chamado(canal_externo=None, aberto_pelo_assistente=False), JULIANA, "Oi"
    )

    assert (conversa.buscas, requisicoes.enviadas) == (0, [])


def test_chamado_migrado_da_vulto_nao_envia(requisicoes, conversa):
    # whatsapp também é o canal dos atendimentos migrados: quem diz que o chamado é do assistente é a coluna
    migrado = chamado(canal_externo="whatsapp", aberto_pelo_assistente=False)

    resposta_ao_cliente.enviar_resposta_ao_cliente(SessaoFalsa(), migrado, JULIANA, "Oi")

    assert (conversa.buscas, requisicoes.enviadas) == (0, [])


def test_falha_no_envio_nao_derruba_a_resposta(requisicoes, conversa):
    requisicoes.resposta = httpx2.Response(403, json={"ok": False})

    resposta_ao_cliente.enviar_resposta_ao_cliente(SessaoFalsa(), chamado(), JULIANA, "Oi")


# ---------- resposta da equipe no WhatsApp ----------

def test_resposta_chega_pelo_whatsapp_dentro_da_janela(requisicoes, no_whatsapp, emails):
    resposta_ao_cliente.enviar_resposta_ao_cliente(SessaoFalsa(), chamado(canal_externo="whatsapp"), JULIANA,
                                                   "Pode trocar na loja.")

    assert textos_do_whatsapp(requisicoes) == ["👤 Juliana, do atendimento:\nPode trocar na loja."]
    assert requisicoes.enviadas[0][1]["json"]["to"] == "5511999998888"
    assert emails.enviados == []


def test_resposta_longa_no_whatsapp_e_cortada_com_o_link(requisicoes, no_whatsapp, emails):
    resposta_ao_cliente.enviar_resposta_ao_cliente(SessaoFalsa(), chamado(canal_externo="whatsapp"), JULIANA,
                                                   "a" * 5000)

    [texto] = textos_do_whatsapp(requisicoes)
    assert len(texto) == whatsapp.TAMANHO_MAXIMO
    assert texto.endswith("(continua no site: https://loja.example/loja/chamados/12)")


def test_fora_da_janela_de_24h_avisa_por_email(requisicoes, no_whatsapp, emails):
    no_whatsapp.conversa.ultima_msg_cliente_em = AGORA - timedelta(hours=25)

    resposta_ao_cliente.enviar_resposta_ao_cliente(SessaoFalsa(), chamado(canal_externo="whatsapp"), JULIANA,
                                                   "Pode trocar na loja.")

    assert requisicoes.enviadas == []
    assert emails.enviados == [(
        "ana@example.com", "Resposta no seu chamado #12",
        "👤 Juliana, do atendimento:\nPode trocar na loja.\n\n"
        "Para ver o chamado: https://loja.example/loja/chamados/12\n",
    )]


def test_conversa_sem_mensagem_do_cliente_avisa_por_email(requisicoes, no_whatsapp, emails):
    no_whatsapp.conversa.ultima_msg_cliente_em = None

    resposta_ao_cliente.enviar_resposta_ao_cliente(SessaoFalsa(), chamado(canal_externo="whatsapp"), JULIANA, "Oi")

    assert requisicoes.enviadas == []
    assert len(emails.enviados) == 1


def test_meta_recusa_e_o_cliente_recebe_por_email(requisicoes, no_whatsapp, emails):
    requisicoes.resposta = httpx2.Response(400, json={"error": {"code": 131047}})

    resposta_ao_cliente.enviar_resposta_ao_cliente(SessaoFalsa(), chamado(canal_externo="whatsapp"), JULIANA, "Oi")

    assert len(requisicoes.enviadas) == 1
    assert [assunto for _, assunto, _ in emails.enviados] == ["Resposta no seu chamado #12"]


def test_email_que_falha_nao_derruba_a_resposta(requisicoes, no_whatsapp, emails):
    no_whatsapp.conversa.ultima_msg_cliente_em = AGORA - timedelta(days=3)
    emails.erro = ErroEmail("Brevo sem resposta")

    resposta_ao_cliente.enviar_resposta_ao_cliente(SessaoFalsa(), chamado(canal_externo="whatsapp"), JULIANA, "Oi")


def test_chamado_sem_cliente_nao_tenta_o_email(requisicoes, no_whatsapp, emails):
    no_whatsapp.conversa.ultima_msg_cliente_em = AGORA - timedelta(days=3)
    sem_cliente = chamado(canal_externo="whatsapp")
    sem_cliente.id_cliente = None

    resposta_ao_cliente.enviar_resposta_ao_cliente(SessaoFalsa(), sem_cliente, JULIANA, "Oi")

    assert emails.enviados == []


def test_telegram_nao_olha_a_janela_de_24h(requisicoes, conversa, emails):
    conversa.conversa.ultima_msg_cliente_em = AGORA - timedelta(days=3)

    resposta_ao_cliente.enviar_resposta_ao_cliente(SessaoFalsa(), chamado(), JULIANA, "Oi")

    assert textos(requisicoes) == ["👤 Juliana, do atendimento:\nOi"]
    assert emails.enviados == []


# ---------- chamado concluído ----------

def test_concluido_avisa_e_devolve_a_conversa_ao_assistente(requisicoes, conversa):
    db = SessaoFalsa()

    resposta_ao_cliente.avisar_chamado_concluido(db, chamado(status="concluido"))

    assert textos(requisicoes) == ["✅ Seu chamado #12 foi concluído pela equipe. Se precisar de mais alguma coisa, "
                                   "é só escrever por aqui."]
    assert (conversa.conversa.etapa, conversa.conversa.id_chamado) == ("livre", None)
    assert db.commits == 1


def test_concluido_sem_conversa_ligada_nao_faz_nada(requisicoes, conversa):
    conversa.conversa = None

    resposta_ao_cliente.avisar_chamado_concluido(SessaoFalsa(), chamado(status="concluido"))

    assert requisicoes.enviadas == []


def test_concluido_no_whatsapp_dentro_da_janela(requisicoes, no_whatsapp, emails):
    resposta_ao_cliente.avisar_chamado_concluido(SessaoFalsa(), chamado(canal_externo="whatsapp", status="concluido"))

    assert textos_do_whatsapp(requisicoes) == ["✅ Seu chamado #12 foi concluído pela equipe. Se precisar de mais "
                                               "alguma coisa, é só escrever por aqui."]
    assert (no_whatsapp.conversa.etapa, no_whatsapp.conversa.id_chamado) == ("livre", None)


def test_concluido_fora_da_janela_avisa_por_email_e_devolve_a_conversa(requisicoes, no_whatsapp, emails):
    no_whatsapp.conversa.ultima_msg_cliente_em = AGORA - timedelta(days=2)
    db = SessaoFalsa()

    resposta_ao_cliente.avisar_chamado_concluido(db, chamado(canal_externo="whatsapp", status="concluido"))

    assert requisicoes.enviadas == []
    assert emails.enviados == [(
        "ana@example.com", "Seu chamado #12 foi concluído",
        "✅ Seu chamado #12 foi concluído pela equipe. Se precisar de mais alguma coisa, é só chamar a gente no "
        "WhatsApp.\n\nPara ver o chamado: https://loja.example/loja/chamados/12\n",
    )]
    assert (no_whatsapp.conversa.etapa, no_whatsapp.conversa.id_chamado, db.commits) == ("livre", None, 1)


# ---------- ligação com o atendimento ----------

@pytest.fixture
def chamado_do_assistente(monkeypatch):
    aberto = chamado()
    monkeypatch.setattr(atendimento_repository, "buscar_chamado", lambda db, i: aberto if i == 12 else None)
    return aberto


def test_mensagem_da_equipe_chega_ao_cliente(requisicoes, conversa, chamado_do_assistente):
    atendimento.enviar_mensagem_da_equipe(SessaoFalsa(), JULIANA, 12, "Já separamos a peça.", False)

    assert textos(requisicoes) == ["👤 Juliana, do atendimento:\nJá separamos a peça."]


def test_mensagem_interna_nao_chega_ao_cliente(requisicoes, conversa, chamado_do_assistente):
    atendimento.enviar_mensagem_da_equipe(SessaoFalsa(), JULIANA, 12, "Cliente pediu G", True)

    assert requisicoes.enviadas == []
