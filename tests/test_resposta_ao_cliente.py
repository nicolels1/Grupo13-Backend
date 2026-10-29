import logging
import uuid
from types import SimpleNamespace

import httpx2
import pytest

from src.config import settings
from src.models.assistente import ConversaCanal
from src.models.atendimento import Chamado
from src.repositories import assistente_repository, atendimento_repository
from src.use_cases import atendimento, resposta_ao_cliente
from src.utils import telegram
from src.utils.telegram import ErroTelegram, enviar_telegram
from tests.apoio import SessaoFalsa, funcionario

TOKEN = "123:segredo-do-bot"
CHAT_ID = "987654321"
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


def textos(requisicoes) -> list[str]:
    return [kwargs["json"]["text"] for _, kwargs in requisicoes.enviadas]


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


def test_whatsapp_ainda_nao_envia(requisicoes, conversa):
    conversa.conversa.canal = "whatsapp"

    resposta_ao_cliente.enviar_resposta_ao_cliente(SessaoFalsa(), chamado(canal_externo="whatsapp"), JULIANA, "Oi")

    assert requisicoes.enviadas == []


def test_falha_no_envio_nao_derruba_a_resposta(requisicoes, conversa):
    requisicoes.resposta = httpx2.Response(403, json={"ok": False})

    resposta_ao_cliente.enviar_resposta_ao_cliente(SessaoFalsa(), chamado(), JULIANA, "Oi")


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
