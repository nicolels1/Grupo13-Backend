import re
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.app import app
from src.config import settings
from src.database.session import get_db
from src.models.assistente import (
    AfirmacaoAssistente, AvisoPontosVencendo, CodigoVerificacao, ConversaCanal, MensagemConversa, ResumoConversa,
)
from src.models.atendimento import Chamado, Mensagem
from src.models.contas import ClienteSemConta, Usuario
from src.models.estoque import Unidade
from src.models.fidelidade import LotePontos, RegraFidelidade
from src.models.vendas import Pedido
from src.repositories import assistente_repository, cliente_sem_conta_repository, fidelidade_repository
from src.repositories import atendimento_repository, pedido_repository, usuario_repository
from src.use_cases import assistente, assistente_conta, resumo_conversa
from src.use_cases.erros import Conflito, MuitasTentativas, NaoAutenticado, RecursoNaoEncontrado, RegraDeNegocio
from src.use_cases.horarios import BRASILIA
from src.utils.cpf import digito_verificador
from src.utils.email import ErroEmail
from tests.apoio import SessaoFalsa

CHAVE = "chave-de-teste"
CANAL, TELEFONE = "whatsapp", "+5511999998888"
AGORA = datetime(2026, 10, 28, 15, 0, tzinfo=BRASILIA)


def _cpf_de(base: str) -> str:
    cpf = base + digito_verificador(base)
    return cpf + digito_verificador(cpf)


CPF_ANA = _cpf_de("390533447")
CPF_SEM_CONTA = _cpf_de("714602938")
CPF_SEM_CADASTRO = _cpf_de("258147369")
ANA = Usuario(id_usuario=uuid.UUID("11111111-1111-1111-1111-111111111111"), nome="Ana Souza",
              email="ana@email.example", cpf=CPF_ANA, tipo_conta="cliente", status_conta="ativa")
SEM_CONTA = ClienteSemConta(id_cliente_sem_conta=7, cpf=CPF_SEM_CONTA, email="bia@email.example", nome="Bia Lima")
PAULISTA = Unidade(id_unidade=2, nome="Loja Paulista", tipo="loja", marca="lorenzi", rua="Avenida Paulista",
                   numero="1000", complemento=None, bairro="Bela Vista", cidade="São Paulo", uf="SP", cep="01310100")


@pytest.fixture
def banco(monkeypatch):
    estado = SimpleNamespace(conversas={}, emails=[], erro_email=None, db=SessaoFalsa(), texto_para_a_conta=[])

    def garantir(db, canal, id_externo):
        chave = (canal, id_externo)
        if chave not in estado.conversas:
            estado.conversas[chave] = ConversaCanal(canal=canal, id_externo=id_externo, etapa="livre", msgs_hoje=0)
        return estado.conversas[chave]

    def codigos(canal, id_externo):
        return [o for o in estado.db.adicionados
                if isinstance(o, CodigoVerificacao) and (o.canal, o.id_externo) == (canal, id_externo)]

    def aberto(db, canal, id_externo, agora):
        validos = [c for c in codigos(canal, id_externo) if c.encerrado_em is None and c.expira_em > agora]
        return validos[-1] if validos else None

    def encerrar(db, canal, id_externo, agora):
        for c in codigos(canal, id_externo):
            if c.encerrado_em is None:
                c.encerrado_em = agora

    def email(para, assunto, texto):
        if estado.erro_email:
            raise estado.erro_email
        estado.emails.append((para, texto))

    m = monkeypatch.setattr
    m(settings, "ASSISTENTE_CHAVE", CHAVE)
    m(assistente_repository, "garantir_conversa", garantir)
    m(assistente_repository, "buscar_conversa",
      lambda db, canal, id_externo, travar=False: estado.conversas.get((canal, id_externo)))
    m(assistente_repository, "codigos_desde",
      lambda db, canal, id_externo, desde: sum(c.criado_em >= desde for c in codigos(canal, id_externo)))
    m(assistente_repository, "codigo_aberto", aberto)
    m(assistente_repository, "encerrar_codigos", encerrar)
    m(assistente_repository, "buscar_resumo", lambda db, id_resumo: next(
        (o for o in estado.db.adicionados if isinstance(o, ResumoConversa) and o.id_resumo == id_resumo), None))
    m(usuario_repository, "buscar_cliente_por_cpf", lambda db, cpf: ANA if cpf == CPF_ANA else None)
    m(usuario_repository, "buscar_por_id", lambda db, id_usuario: ANA)
    m(cliente_sem_conta_repository, "buscar_por_cpf", lambda db, cpf: SEM_CONTA if cpf == CPF_SEM_CONTA else None)
    m(cliente_sem_conta_repository, "buscar", lambda db, id_cliente_sem_conta: SEM_CONTA)
    m(assistente_conta, "enviar_email", email)
    m(assistente_repository, "passar_texto_para_a_conta",
      lambda db, canal, id_externo, desde, id_cliente, id_cliente_sem_conta:
      estado.texto_para_a_conta.append((desde, id_cliente, id_cliente_sem_conta)))
    # ANA é a mesma em todos os testes: o lembrete volta ao começo em cada um
    m(ANA, "lembrete_pontos_aceito_em", None)
    m(ANA, "lembrete_pontos_oferecido_em", None)
    return estado


def mensagem(banco, momento=AGORA):
    return assistente_conta.registrar_mensagem(banco.db, CANAL, TELEFONE, momento)


def codigo_do_email(banco) -> str:
    return re.search(r"\b(\d{6})\b", banco.emails[-1][1]).group(1)


def confirmar(banco, cpf=CPF_ANA, momento=AGORA):
    mensagem(banco, momento)
    assistente_conta.pedir_codigo(banco.db, CANAL, TELEFONE, cpf, momento)
    return assistente_conta.conferir_codigo(banco.db, CANAL, TELEFONE, codigo_do_email(banco), momento)


# ---------- conversa ----------

def test_primeira_mensagem_cria_a_conversa(banco):
    estado = mensagem(banco)

    assert estado == {"etapa": "livre", "confirmado": False, "conversa_nova": True, "limite_atingido": False,
                      "avisar_limite": False, "bloqueado_ate": None, "id_chamado": None,
                      "equipe_disponivel": True, "equipe_volta_em": None, "aviso_equipe": None}
    assert banco.conversas[(CANAL, TELEFONE)].ultima_msg_cliente_em == AGORA


def test_mensagem_dentro_de_30_min_continua_a_conversa_e_a_confirmacao(banco):
    confirmar(banco)

    estado = mensagem(banco, AGORA + timedelta(minutes=29))

    assert (estado["conversa_nova"], estado["confirmado"]) == (False, True)
    assert banco.conversas[(CANAL, TELEFONE)].confirmado_ate == AGORA + timedelta(minutes=59)


def test_depois_de_30_min_parada_a_conversa_e_nova_e_pede_o_codigo_de_novo(banco):
    confirmar(banco)

    estado = mensagem(banco, AGORA + timedelta(minutes=31))

    assert (estado["conversa_nova"], estado["confirmado"]) == (True, False)
    assert banco.conversas[(CANAL, TELEFONE)].id_cliente is None


def test_limite_do_dia_avisa_uma_vez_e_zera_no_dia_seguinte(banco):
    for _ in range(100):
        estado = mensagem(banco)
    assert estado["limite_atingido"] is False

    estado = mensagem(banco)
    assert (estado["limite_atingido"], estado["avisar_limite"]) == (True, True)
    estado = mensagem(banco)
    assert (estado["limite_atingido"], estado["avisar_limite"]) == (True, False)

    estado = mensagem(banco, AGORA + timedelta(days=1))
    assert estado["limite_atingido"] is False


def test_n8n_poe_a_conversa_esperando_o_cpf(banco):
    mensagem(banco)
    assert assistente_conta.mudar_etapa(banco.db, CANAL, TELEFONE, "esperando_cpf", AGORA)["etapa"] == "esperando_cpf"


def test_ja_confirmado_nao_volta_a_esperar_o_cpf(banco):
    confirmar(banco)
    assert assistente_conta.mudar_etapa(banco.db, CANAL, TELEFONE, "esperando_cpf", AGORA)["etapa"] == "livre"


def test_conversa_nao_registrada_nao_pede_codigo(banco):
    with pytest.raises(RecursoNaoEncontrado):
        assistente_conta.pedir_codigo(banco.db, CANAL, TELEFONE, CPF_ANA, AGORA)


# ---------- CPF e código ----------

def test_cpf_com_conta_manda_o_codigo_para_o_email_do_cadastro(banco):
    mensagem(banco)

    resposta = assistente_conta.pedir_codigo(banco.db, CANAL, TELEFONE, "390.533.447-" + CPF_ANA[-2:], AGORA)

    assert resposta == {"mensagem": assistente_conta.RESPOSTA_DO_CPF, "validade_minutos": 10}
    [(para, texto)] = banco.emails
    assert para == "ana@email.example" and "Olá, Ana!" in texto
    assert banco.conversas[(CANAL, TELEFONE)].etapa == "esperando_codigo"
    [codigo] = [o for o in banco.db.adicionados if isinstance(o, CodigoVerificacao)]
    assert codigo_do_email(banco) not in codigo.codigo_hash  # só o hash fica guardado
    assert codigo.expira_em == AGORA + timedelta(minutes=10)


def test_cpf_sem_cadastro_responde_igual_e_nao_manda_email(banco):
    mensagem(banco)

    resposta = assistente_conta.pedir_codigo(banco.db, CANAL, TELEFONE, CPF_SEM_CADASTRO, AGORA)

    assert resposta["mensagem"] == assistente_conta.RESPOSTA_DO_CPF
    assert banco.emails == []
    with pytest.raises(RegraDeNegocio, match="incorreto"):
        assistente_conta.conferir_codigo(banco.db, CANAL, TELEFONE, "123456", AGORA)


def test_falha_no_email_responde_igual(banco):
    mensagem(banco)
    banco.erro_email = ErroEmail("Brevo fora")

    resposta = assistente_conta.pedir_codigo(banco.db, CANAL, TELEFONE, CPF_ANA, AGORA)

    assert resposta["mensagem"] == assistente_conta.RESPOSTA_DO_CPF


def test_cpf_invalido(banco):
    mensagem(banco)
    with pytest.raises(RegraDeNegocio, match="CPF inválido"):
        assistente_conta.pedir_codigo(banco.db, CANAL, TELEFONE, "11111111111", AGORA)


def test_no_maximo_tres_codigos_por_dia(banco):
    mensagem(banco)
    for _ in range(3):
        assistente_conta.pedir_codigo(banco.db, CANAL, TELEFONE, CPF_SEM_CADASTRO, AGORA)

    with pytest.raises(MuitasTentativas):
        assistente_conta.pedir_codigo(banco.db, CANAL, TELEFONE, CPF_ANA, AGORA)


def test_codigo_certo_liga_a_conversa_a_conta(banco):
    assert confirmar(banco) == {"confirmado": True, "nome": "Ana"}

    conversa = banco.conversas[(CANAL, TELEFONE)]
    assert (conversa.id_cliente, conversa.etapa) == (ANA.id_usuario, "livre")
    assert conversa.confirmado_ate == AGORA + timedelta(minutes=30)


def test_cliente_sem_conta_da_migracao_tambem_confirma(banco):
    assert confirmar(banco, CPF_SEM_CONTA)["nome"] == "Bia"
    assert banco.conversas[(CANAL, TELEFONE)].id_cliente_sem_conta == 7


def test_pedir_outro_codigo_invalida_o_anterior(banco):
    mensagem(banco)
    assistente_conta.pedir_codigo(banco.db, CANAL, TELEFONE, CPF_ANA, AGORA)
    primeiro = codigo_do_email(banco)
    assistente_conta.pedir_codigo(banco.db, CANAL, TELEFONE, CPF_ANA, AGORA)
    if primeiro == codigo_do_email(banco):
        pytest.skip("os dois códigos sortearam o mesmo número")

    with pytest.raises(RegraDeNegocio, match="incorreto"):
        assistente_conta.conferir_codigo(banco.db, CANAL, TELEFONE, primeiro, AGORA)


def test_codigo_expira_em_10_min(banco):
    mensagem(banco)
    assistente_conta.pedir_codigo(banco.db, CANAL, TELEFONE, CPF_ANA, AGORA)

    with pytest.raises(RegraDeNegocio, match="expirou"):
        depois = AGORA + timedelta(minutes=11)
        assistente_conta.conferir_codigo(banco.db, CANAL, TELEFONE, codigo_do_email(banco), depois)
    assert banco.conversas[(CANAL, TELEFONE)].etapa == "esperando_cpf"


def test_cinco_erros_pausam_a_confirmacao_por_30_min(banco):
    mensagem(banco)
    assistente_conta.pedir_codigo(banco.db, CANAL, TELEFONE, CPF_ANA, AGORA)
    errado = "000000" if codigo_do_email(banco) != "000000" else "111111"

    for restam in (4, 3, 2, 1):
        with pytest.raises(RegraDeNegocio, match=f"{restam} tentativa"):
            assistente_conta.conferir_codigo(banco.db, CANAL, TELEFONE, errado, AGORA)
    with pytest.raises(MuitasTentativas, match="15:30"):
        assistente_conta.conferir_codigo(banco.db, CANAL, TELEFONE, errado, AGORA)

    # pausado, nem o código certo nem um CPF novo passam
    with pytest.raises(MuitasTentativas):
        assistente_conta.pedir_codigo(banco.db, CANAL, TELEFONE, CPF_ANA, AGORA + timedelta(minutes=29))
    assistente_conta.pedir_codigo(banco.db, CANAL, TELEFONE, CPF_ANA, AGORA + timedelta(minutes=31))


# ---------- pontos ----------

def saldo(marca="vulto", **campos):
    return {"marca": marca, "saldo": 1220, "valor_em_reais": Decimal("12.20"), "categoria": "prata",
            "ganhos_no_ano": 620, "proximo_vencimento": datetime(2027, 3, 12, 3, 0, tzinfo=BRASILIA),
            "pontos_no_proximo_vencimento": 320, **campos}


@pytest.fixture
def pontos(banco, monkeypatch):
    dados = SimpleNamespace(saldos=[saldo()], sem_data={"vulto": 900})
    m = monkeypatch.setattr
    m(fidelidade_repository, "resumo", lambda db, id_cliente, id_sem_conta: dados.saldos)
    m(fidelidade_repository, "regra_vigente",
      lambda db, marca, hoje: RegraFidelidade(marca=marca, limite_prata=500, limite_ouro=1000))
    m(assistente_repository, "pontos_sem_vencimento", lambda db, id_cliente, id_sem_conta: dados.sem_data)
    return dados


def test_pontos_exigem_a_conta_confirmada(banco, pontos):
    mensagem(banco)
    with pytest.raises(NaoAutenticado):
        assistente_conta.meus_pontos(banco.db, CANAL, TELEFONE, AGORA)


def test_pontos_por_marca_com_vencimento_e_quanto_falta(banco, pontos):
    confirmar(banco)

    resposta = assistente_conta.meus_pontos(banco.db, CANAL, TELEFONE, AGORA)

    [vulto] = resposta["marcas"]
    assert vulto["proximo_vencimento"] == {"data": date(2027, 3, 12), "pontos": 320}
    assert vulto["pontos_sem_vencimento"] == 900
    assert (vulto["proxima_categoria"], vulto["faltam_para_subir"]) == ("ouro", 380)
    assert resposta["texto"] == (
        "Vulto: 1.220 pontos (R$ 12,20), categoria Prata. 320 pontos vencem em 12/03/2027 e 900 ainda não têm "
        "data de vencimento. Faltam 380 pontos ganhos este ano para chegar a Ouro."
    )


def test_ouro_nao_tem_proxima_categoria_e_lote_sem_data_nao_promete_nada(banco, pontos):
    pontos.saldos = [saldo(categoria="ouro", proximo_vencimento=None, pontos_no_proximo_vencimento=0)]
    pontos.sem_data = {"vulto": 1220}
    confirmar(banco)

    [vulto] = assistente_conta.meus_pontos(banco.db, CANAL, TELEFONE, AGORA)["marcas"]

    assert (vulto["proxima_categoria"], vulto["faltam_para_subir"]) == (None, None)
    assert vulto["texto"].endswith("categoria Ouro. Ainda não têm data de vencimento.")
    assert "nunca" not in vulto["texto"]


def test_sem_pontos(banco, pontos):
    pontos.saldos, pontos.sem_data = [], {}
    confirmar(banco)
    resposta = assistente_conta.meus_pontos(banco.db, CANAL, TELEFONE, AGORA)
    assert resposta["texto"].startswith("Você ainda não tem pontos")


def test_pontos_ficam_registrados_para_auditoria(banco, pontos):
    confirmar(banco)

    assistente_conta.meus_pontos(banco.db, CANAL, TELEFONE, AGORA)

    [afirmacao] = [o for o in banco.db.adicionados if isinstance(o, AfirmacaoAssistente)]
    assert (afirmacao.ferramenta, afirmacao.id_cliente) == ("meus_pontos", ANA.id_usuario)
    assert afirmacao.dados["marcas"][0]["saldo"] == 1220
    assert afirmacao.dados["marcas"][0]["proximo_vencimento"]["data"] == "2027-03-12"


# ---------- pedidos ----------

def pedido(id_pedido, status, **campos):
    return Pedido(id_pedido=id_pedido, codigo_venda=f"CL0000000{id_pedido}", id_cliente=ANA.id_usuario,
                  id_unidade=2, canal_venda="site", modalidade="retirada", status=status, valor_total=Decimal("129.90"),
                  criado_em=datetime(2026, 10, 20, 12, 0, tzinfo=BRASILIA), **campos)


@pytest.fixture
def pedidos(banco, monkeypatch):
    dados = SimpleNamespace(linhas=[])
    item = {"produto": "Camisa Social", "cor": "Azul", "tamanho": "G", "quantidade": 1}
    m = monkeypatch.setattr
    m(pedido_repository, "listar_pedidos",
      lambda db, limit, offset, **filtros: (dados.linhas[:limit], len(dados.linhas)))
    m(pedido_repository, "pedido_com_nomes",
      lambda db, id_pedido: next((linha for linha in dados.linhas if linha[0].id_pedido == id_pedido), None))
    m(pedido_repository, "itens_dos_pedidos", lambda db, ids: {i: [item] for i in ids})
    m(assistente_repository, "unidades", lambda db, ids: {2: PAULISTA})
    return dados


def test_pedido_pronto_diz_onde_e_ate_quando_retirar(banco, pedidos):
    pronto = pedido(1, "pronto_para_retirada", pronto_retirada_em=datetime(2026, 10, 27, 10, 0, tzinfo=BRASILIA))
    pedidos.linhas = [(pronto, "Ana Souza", "Loja Paulista")]
    confirmar(banco)

    [montado] = assistente_conta.meus_pedidos(banco.db, CANAL, TELEFONE, None, AGORA)["items"]

    assert montado["retirada"]["retirar_ate"] == date(2026, 11, 3)
    assert montado["texto"] == (
        "Pedido CL00000001 (Lorenzi), de 20/10/2026: pronto para retirar na Loja Paulista (Avenida Paulista, 1000 - "
        "Bela Vista, São Paulo/SP) até 03/11/2026. Itens: 1x Camisa Social (Azul, G). Total R$ 129,90."
    )


def test_pedido_aguardando_pagamento_diz_ate_quando_pagar(banco, pedidos):
    expira = datetime(2026, 10, 28, 15, 10, tzinfo=BRASILIA)
    pedidos.linhas = [(pedido(2, "aguardando_pagamento", reserva_expira_em=expira), "Ana", "Loja Paulista")]
    confirmar(banco)

    [montado] = assistente_conta.meus_pedidos(banco.db, CANAL, TELEFONE, None, AGORA)["items"]

    assert "aguardando pagamento" in montado["texto"] and "28/10/2026 às 15:10" in montado["texto"]


def test_pedido_de_outra_pessoa_responde_como_inexistente(banco, pedidos):
    de_outro = pedido(3, "pago")
    de_outro.id_cliente = uuid.uuid4()
    pedidos.linhas = [(de_outro, "Outro", "Loja Paulista")]
    confirmar(banco)

    with pytest.raises(RecursoNaoEncontrado):
        assistente_conta.meus_pedidos(banco.db, CANAL, TELEFONE, 3, AGORA)


def test_cliente_sem_conta_nao_tem_pedidos_do_site(banco, pedidos):
    confirmar(banco, CPF_SEM_CONTA)

    resposta = assistente_conta.meus_pedidos(banco.db, CANAL, TELEFONE, None, AGORA)

    assert resposta == {"items": [], "texto": assistente_conta.SEM_PEDIDOS_NO_SITE}


# ---------- rotas ----------

@pytest.fixture
def cliente(banco):
    app.dependency_overrides[get_db] = lambda: banco.db
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_rota_da_conversa_exige_a_chave(cliente):
    resposta = cliente.post("/assistente/conversa", json={"canal": CANAL, "id_externo": TELEFONE})
    assert resposta.status_code == 401


def test_rota_da_conversa_responde_o_estado(cliente):
    resposta = cliente.post("/assistente/conversa", json={"canal": CANAL, "id_externo": TELEFONE},
                            headers={"X-Assistente-Chave": CHAVE})

    assert resposta.status_code == 200
    assert resposta.json()["etapa"] == "livre"


def test_rota_aceita_o_telegram(cliente):
    resposta = cliente.post("/assistente/conversa", json={"canal": "telegram", "id_externo": "123456789"},
                            headers={"X-Assistente-Chave": CHAVE})
    assert resposta.status_code == 200


def test_rota_recusa_canal_desconhecido(cliente):
    # instagram existe no chamado migrado da Vulto, não no assistente
    resposta = cliente.post("/assistente/conversa", json={"canal": "instagram", "id_externo": TELEFONE},
                            headers={"X-Assistente-Chave": CHAVE})
    assert resposta.status_code == 422


def test_rota_de_pontos_sem_confirmar_responde_401(cliente):
    cabecalho = {"X-Assistente-Chave": CHAVE}
    cliente.post("/assistente/conversa", json={"canal": CANAL, "id_externo": TELEFONE}, headers=cabecalho)

    resposta = cliente.post("/assistente/meus-pontos", json={"canal": CANAL, "id_externo": TELEFONE}, headers=cabecalho)

    assert resposta.status_code == 401


# ---------- resumo anônimo da conversa ----------

def resumos(banco) -> list[ResumoConversa]:
    return [o for o in banco.db.adicionados if isinstance(o, ResumoConversa)]


def resumo_atual(banco) -> ResumoConversa:
    conversa = banco.conversas[(CANAL, TELEFONE)]
    return next(r for r in resumos(banco) if r.id_resumo == conversa.id_resumo_atual)


def test_conversa_nova_abre_um_resumo_na_hora_cheia(banco):
    mensagem(banco, AGORA + timedelta(minutes=47, seconds=12))

    [resumo] = resumos(banco)
    assert (resumo.canal, resumo.iniciada_em) == (CANAL, AGORA)
    assert (resumo.autenticou, resumo.assuntos, resumo.ferramenta_falhou, resumo.avaliacao) == (False, [], False, None)


def test_mesma_conversa_usa_o_mesmo_resumo_e_a_nova_troca(banco):
    mensagem(banco)
    mensagem(banco, AGORA + timedelta(minutes=10))
    assert len(resumos(banco)) == 1

    mensagem(banco, AGORA + timedelta(minutes=41))

    primeiro, segundo = resumos(banco)
    assert banco.conversas[(CANAL, TELEFONE)].id_resumo_atual == segundo.id_resumo != primeiro.id_resumo


def test_conversa_de_antes_do_resumo_ganha_um(banco):
    mensagem(banco)
    banco.conversas[(CANAL, TELEFONE)].id_resumo_atual = None

    mensagem(banco, AGORA + timedelta(minutes=5))

    assert banco.conversas[(CANAL, TELEFONE)].id_resumo_atual is not None


def test_codigo_certo_marca_que_autenticou(banco):
    confirmar(banco)
    assert resumo_atual(banco).autenticou is True


def test_codigo_errado_nao_marca(banco):
    mensagem(banco)
    assistente_conta.pedir_codigo(banco.db, CANAL, TELEFONE, CPF_SEM_CADASTRO, AGORA)
    with pytest.raises(RegraDeNegocio):
        assistente_conta.conferir_codigo(banco.db, CANAL, TELEFONE, "123456", AGORA)
    assert resumo_atual(banco).autenticou is False


def test_ferramenta_anota_o_assunto_uma_vez(banco):
    mensagem(banco)
    for _ in range(2):
        resumo_conversa.com_assunto(banco.db, CANAL, TELEFONE, "pecas", lambda: "ok")
    resumo_conversa.com_assunto(banco.db, CANAL, TELEFONE, "lojas", lambda: "ok")

    assert resumo_atual(banco).assuntos == ["pecas", "lojas"]


def test_erro_de_regra_anota_o_assunto_sem_falha(banco):
    mensagem(banco)

    def sem_a_cor():
        raise RecursoNaoEncontrado("Camisa não existe na cor verde")

    with pytest.raises(RecursoNaoEncontrado):
        resumo_conversa.com_assunto(banco.db, CANAL, TELEFONE, "estoque", sem_a_cor)
    assert (resumo_atual(banco).assuntos, resumo_atual(banco).ferramenta_falhou) == (["estoque"], False)


def test_erro_inesperado_marca_a_falha(banco):
    mensagem(banco)

    def quebrada():
        raise RuntimeError("banco fora do ar")

    with pytest.raises(RuntimeError):
        resumo_conversa.com_assunto(banco.db, CANAL, TELEFONE, "lojas", quebrada)
    assert resumo_atual(banco).ferramenta_falhou is True
    assert banco.db.rollbacks == 1


def test_sem_a_conversa_a_ferramenta_responde_e_nao_anota(banco):
    assert resumo_conversa.com_assunto(banco.db, None, None, "pecas", lambda: "ok") == "ok"
    assert resumos(banco) == []


def test_n8n_registra_avaliacao_e_fora_do_tema(banco):
    mensagem(banco)

    resumo_conversa.registrar_do_n8n(banco.db, CANAL, TELEFONE, "negativa", fora_do_tema=True)
    resumo_conversa.registrar_do_n8n(banco.db, CANAL, TELEFONE, None, fora_do_tema=True)

    resumo = resumo_atual(banco)
    assert (resumo.avaliacao, resumo.assuntos) == ("negativa", ["fora_do_tema"])


def test_n8n_sem_conversa_registrada(banco):
    with pytest.raises(RecursoNaoEncontrado):
        resumo_conversa.registrar_do_n8n(banco.db, CANAL, TELEFONE, "positiva", fora_do_tema=False)


def test_resumo_nao_guarda_quem_e(banco):
    confirmar(banco)
    colunas = {c.key for c in ResumoConversa.__table__.columns}
    assert not colunas & {"id_externo", "id_cliente", "id_cliente_sem_conta", "telefone", "texto"}


def test_rota_de_lojas_anota_pelo_cabecalho(cliente, banco, monkeypatch):
    monkeypatch.setattr(assistente, "lojas", lambda db, agora: {"items": []})
    cabecalho = {"X-Assistente-Chave": CHAVE}
    cliente.post("/assistente/conversa", json={"canal": CANAL, "id_externo": TELEFONE}, headers=cabecalho)

    resposta = cliente.get("/assistente/lojas", headers={**cabecalho, "X-Canal": CANAL, "X-Id-Externo": TELEFONE})

    assert resposta.status_code == 200
    assert resumo_atual(banco).assuntos == ["lojas"]


def test_rota_de_pontos_anota_o_assunto_mesmo_sem_confirmar(cliente, banco):
    cabecalho = {"X-Assistente-Chave": CHAVE}
    corpo = {"canal": CANAL, "id_externo": TELEFONE}
    cliente.post("/assistente/conversa", json=corpo, headers=cabecalho)

    assert cliente.post("/assistente/meus-pontos", json=corpo, headers=cabecalho).status_code == 401
    assert resumo_atual(banco).assuntos == ["pontos"]


def test_rota_do_resumo_do_n8n(cliente, banco):
    cabecalho = {"X-Assistente-Chave": CHAVE}
    corpo = {"canal": CANAL, "id_externo": TELEFONE}
    cliente.post("/assistente/conversa", json=corpo, headers=cabecalho)

    resposta = cliente.patch("/assistente/conversa/resumo", json={**corpo, "avaliacao": "positiva"}, headers=cabecalho)

    assert resposta.status_code == 204
    assert resumo_atual(banco).avaliacao == "positiva"


def test_rota_do_resumo_recusa_avaliacao_desconhecida(cliente):
    resposta = cliente.patch("/assistente/conversa/resumo",
                             json={"canal": CANAL, "id_externo": TELEFONE, "avaliacao": "talvez"},
                             headers={"X-Assistente-Chave": CHAVE})
    assert resposta.status_code == 422



# ---------- chamado aberto pelo assistente ----------

PEDIDO_DE_TROCA = {"categoria": "troca_devolucao", "assunto": "Trocar camisa", "resumo": "Quer trocar a camisa do "
                   "pedido 12 pelo tamanho G.", "id_pedido": None}
FALAS = [{"de": "cliente", "texto": "quero trocar a camisa"}, {"de": "assistente", "texto": "Claro! Qual tamanho?"}]


@pytest.fixture
def chamados(banco, monkeypatch):
    def buscar(db, id_chamado):
        return next((o for o in banco.db.adicionados if isinstance(o, Chamado) and o.id_chamado == id_chamado), None)

    m = monkeypatch.setattr
    m(atendimento_repository, "buscar_chamado", buscar)
    m(atendimento_repository, "detalhar_chamado", lambda db, lado, id_chamado: {"id_chamado": id_chamado})
    return buscar


def rascunhar(banco, dados=PEDIDO_DE_TROCA, momento=AGORA):
    return assistente_conta.rascunhar_chamado(banco.db, CANAL, TELEFONE, dict(dados), momento)


def enviar(banco, momento=AGORA):
    return assistente_conta.abrir_chamado(banco.db, CANAL, TELEFONE, FALAS, momento)


# o caminho inteiro: a IA monta o resumo e o cliente aperta [Enviar]
def abrir(banco, momento=AGORA):
    rascunhar(banco, momento=momento)
    return enviar(banco, momento)


def test_rascunho_espera_o_cliente_conferir(banco, chamados):
    confirmar(banco)

    resposta = rascunhar(banco)

    conversa = banco.conversas[(CANAL, TELEFONE)]
    assert conversa.etapa == "confirmando_chamado"
    assert conversa.rascunho_chamado == PEDIDO_DE_TROCA
    assert "Trocar camisa" in resposta["mensagem"] and PEDIDO_DE_TROCA["resumo"] in resposta["mensagem"]
    assert not [o for o in banco.db.adicionados if isinstance(o, Chamado)]


def test_chamado_abre_com_o_rascunho_corrigido(banco, chamados, sem_prioridade_sugerida_em_segundo_plano):
    confirmar(banco)
    rascunhar(banco)
    rascunhar(banco, {**PEDIDO_DE_TROCA, "resumo": "Quer trocar a camisa pelo tamanho M."})

    chamado = chamados(banco.db, enviar(banco)["id_chamado"])

    assert chamado.descricao.startswith("Quer trocar a camisa pelo tamanho M.")
    assert banco.conversas[(CANAL, TELEFONE)].rascunho_chamado is None


def test_enviar_sem_rascunho_nao_abre_chamado(banco, chamados):
    confirmar(banco)

    with pytest.raises(RegraDeNegocio, match="resumo"):
        enviar(banco)


def test_corrigir_desfaz_o_rascunho(banco, chamados):
    confirmar(banco)
    rascunhar(banco)

    estado = assistente_conta.mudar_etapa(banco.db, CANAL, TELEFONE, "livre", AGORA)

    assert estado["etapa"] == "livre"
    assert banco.conversas[(CANAL, TELEFONE)].rascunho_chamado is None
    with pytest.raises(RegraDeNegocio, match="resumo"):
        enviar(banco)


def test_rascunho_vence_com_a_conversa(banco, chamados):
    confirmar(banco)
    rascunhar(banco)

    estado = mensagem(banco, AGORA + timedelta(minutes=31))

    assert estado["etapa"] == "livre"
    assert banco.conversas[(CANAL, TELEFONE)].rascunho_chamado is None


def test_pedir_o_cpf_no_meio_da_confirmacao_desfaz_o_rascunho(banco, chamados):
    confirmar(banco)
    rascunhar(banco)

    assistente_conta.pedir_codigo(banco.db, CANAL, TELEFONE, CPF_ANA, AGORA)

    conversa = banco.conversas[(CANAL, TELEFONE)]
    assert (conversa.etapa, conversa.rascunho_chamado) == ("esperando_codigo", None)


def test_rascunho_com_pedido_de_outra_pessoa(banco, chamados, monkeypatch):
    monkeypatch.setattr(atendimento_repository, "buscar_pedido",
                        lambda db, id_pedido: Pedido(id_pedido=id_pedido, id_cliente=uuid.uuid4()))
    confirmar(banco)

    with pytest.raises(RecursoNaoEncontrado):
        rascunhar(banco, {**PEDIDO_DE_TROCA, "id_pedido": 99})
    assert banco.conversas[(CANAL, TELEFONE)].etapa == "livre"


def test_abre_o_chamado_com_o_resumo_e_a_conversa(banco, chamados, sem_prioridade_sugerida_em_segundo_plano):
    confirmar(banco)

    resposta = abrir(banco)

    chamado = chamados(banco.db, resposta["id_chamado"])
    assert (chamado.id_cliente, chamado.canal_externo, chamado.contato_externo) == (ANA.id_usuario, CANAL, None)
    assert chamado.aberto_pelo_assistente is True
    assert chamado.descricao == ("Quer trocar a camisa do pedido 12 pelo tamanho G.\n\nConversa com o assistente:\n"
                                 "Cliente: quero trocar a camisa\nAssistente: Claro! Qual tamanho?")
    assert f"#{chamado.id_chamado}" in resposta["mensagem"]
    assert sem_prioridade_sugerida_em_segundo_plano == [chamado.id_chamado]
    conversa = banco.conversas[(CANAL, TELEFONE)]
    assert (conversa.etapa, conversa.id_chamado) == ("em_chamado", chamado.id_chamado)
    assert resumo_atual(banco).virou_chamado is True


def test_conversa_longa_perde_as_falas_mais_antigas_no_chamado():
    conversa = [{"de": "cliente", "texto": f"{i:03d}" + "x" * 996} for i in range(10)]

    descricao = assistente_conta._descricao("Resumo", conversa)

    assert len(descricao) <= assistente_conta.TAMANHO_DA_DESCRICAO
    assert "000x" not in descricao and "009x" in descricao


def test_chamado_exige_a_conta_confirmada(banco, chamados):
    mensagem(banco)
    with pytest.raises(NaoAutenticado):
        abrir(banco)


def test_cliente_sem_conta_recebe_o_link_de_cadastro(banco, chamados):
    confirmar(banco, CPF_SEM_CONTA)
    with pytest.raises(RegraDeNegocio, match="/cadastro"):
        abrir(banco)


def test_nao_abre_dois_chamados_na_mesma_conversa(banco, chamados):
    confirmar(banco)
    primeiro = abrir(banco)

    with pytest.raises(Conflito, match=f"#{primeiro['id_chamado']}"):
        abrir(banco)


def test_mensagem_do_cliente_vai_para_o_chamado(banco, chamados):
    confirmar(banco)
    id_chamado = abrir(banco)["id_chamado"]

    gravada = assistente_conta.mensagem_no_chamado(banco.db, CANAL, TELEFONE, "e o meu pedido?")

    [mensagem_gravada] = [o for o in banco.db.adicionados if isinstance(o, Mensagem)]
    assert (mensagem_gravada.id_chamado, mensagem_gravada.id_autor) == (id_chamado, ANA.id_usuario)
    assert (gravada["conteudo"], gravada["da_equipe"], gravada["interna"]) == ("e o meu pedido?", False, False)


def test_chamado_continua_depois_que_a_confirmacao_vence(banco, chamados):
    confirmar(banco)
    abrir(banco)

    estado = mensagem(banco, AGORA + timedelta(hours=2))

    assert (estado["confirmado"], estado["etapa"]) == (False, "em_chamado")
    assistente_conta.mensagem_no_chamado(banco.db, CANAL, TELEFONE, "alguma novidade?")


def test_confirmar_de_novo_no_meio_do_chamado_volta_para_o_chamado(banco, chamados):
    confirmar(banco)
    abrir(banco)
    depois = AGORA + timedelta(hours=2)
    mensagem(banco, depois)
    assistente_conta.mudar_etapa(banco.db, CANAL, TELEFONE, "esperando_cpf", depois)

    confirmar(banco, momento=depois)

    assert banco.conversas[(CANAL, TELEFONE)].etapa == "em_chamado"


def test_desistir_do_cpf_no_meio_do_chamado_volta_para_o_chamado(banco, chamados):
    confirmar(banco)
    abrir(banco)
    assistente_conta.mudar_etapa(banco.db, CANAL, TELEFONE, "esperando_cpf", AGORA)

    estado = assistente_conta.mudar_etapa(banco.db, CANAL, TELEFONE, "livre", AGORA)

    assert estado["etapa"] == "em_chamado"


def test_chamado_concluido_devolve_a_conversa_ao_assistente(banco, chamados):
    confirmar(banco)
    chamado = chamados(banco.db, abrir(banco)["id_chamado"])
    chamado.status = "concluido"

    estado = mensagem(banco, AGORA + timedelta(minutes=5))

    assert (estado["etapa"], estado["id_chamado"]) == ("livre", None)
    with pytest.raises(RegraDeNegocio, match="não tem chamado"):
        assistente_conta.mensagem_no_chamado(banco.db, CANAL, TELEFONE, "oi")
    assert abrir(banco, momento=AGORA + timedelta(minutes=5))["id_chamado"] != chamado.id_chamado


def test_rota_do_chamado_anota_o_assunto(cliente, banco, chamados):
    cabecalho = {"X-Assistente-Chave": CHAVE}
    confirmar(banco, momento=datetime.now(BRASILIA))  # a rota usa a hora de agora

    conversa = {"canal": CANAL, "id_externo": TELEFONE}

    rascunho = cliente.put("/assistente/chamado/rascunho", json={**conversa, **PEDIDO_DE_TROCA}, headers=cabecalho)
    resposta = cliente.post("/assistente/chamado", json={**conversa, "conversa": FALAS}, headers=cabecalho)

    assert (rascunho.status_code, resposta.status_code) == (200, 201)
    assert resumo_atual(banco).assuntos == ["chamado"]


def test_rota_do_chamado_sem_rascunho_responde_422(cliente, banco, chamados):
    confirmar(banco, momento=datetime.now(BRASILIA))

    resposta = cliente.post("/assistente/chamado", json={"canal": CANAL, "id_externo": TELEFONE, "conversa": FALAS},
                            headers={"X-Assistente-Chave": CHAVE})

    assert resposta.status_code == 422


def test_rota_da_mensagem_sem_chamado_responde_422(cliente, banco, chamados):
    mensagem(banco)

    resposta = cliente.post("/assistente/chamado/mensagem", json={"canal": CANAL, "id_externo": TELEFONE, "texto": "oi"},
                            headers={"X-Assistente-Chave": CHAVE})

    assert resposta.status_code == 422


# ---------- horário da equipe ----------

QUARTA_21H = datetime(2026, 10, 28, 21, 0, tzinfo=BRASILIA)


def test_no_horario_a_equipe_esta_atendendo():
    assert assistente_conta.equipe_volta_em(AGORA) is None


def test_fora_do_horario_diz_quando_a_equipe_volta():
    quinta_8h = datetime(2026, 10, 29, 8, 0, tzinfo=BRASILIA)

    assert assistente_conta.equipe_volta_em(QUARTA_21H) == datetime(2026, 10, 29, 10, 0, tzinfo=BRASILIA)
    assert assistente_conta.equipe_volta_em(quinta_8h) == datetime(2026, 10, 29, 10, 0, tzinfo=BRASILIA)


def test_domingo_e_feriado_a_equipe_nao_atende():
    # sábado à noite: domingo fechado e segunda é Finados, então volta na terça
    sabado_19h = datetime(2026, 10, 31, 19, 0, tzinfo=BRASILIA)
    assert assistente_conta.equipe_volta_em(sabado_19h) == datetime(2026, 11, 3, 10, 0, tzinfo=BRASILIA)


def test_estado_da_conversa_traz_o_aviso_fora_do_horario(banco):
    estado = mensagem(banco, QUARTA_21H)

    assert estado["equipe_disponivel"] is False
    assert estado["aviso_equipe"] == ("A nossa equipe atende de segunda a sexta das 10h às 20h e aos sábados das "
                                      "10h às 18h. Ela volta amanhã às 10h e responde por aqui.")


def test_aviso_com_dia_da_semana_quando_nao_e_amanha(banco):
    estado = mensagem(banco, datetime(2026, 10, 31, 19, 0, tzinfo=BRASILIA))
    assert "Ela volta terça (03/11) às 10h" in estado["aviso_equipe"]


def test_chamado_fora_do_horario_avisa_quando_a_equipe_volta(banco, chamados,
                                                             sem_prioridade_sugerida_em_segundo_plano):
    confirmar(banco, momento=QUARTA_21H)

    resposta = abrir(banco, QUARTA_21H)

    assert resposta["mensagem"].startswith(f"Pronto, abri o chamado #{resposta['id_chamado']}. A nossa equipe")
    assert "volta amanhã às 10h" in resposta["mensagem"]


def test_chamado_no_horario_nao_fala_do_horario(banco, chamados, sem_prioridade_sugerida_em_segundo_plano):
    confirmar(banco)
    assert abrir(banco)["mensagem"].endswith("A equipe responde por aqui mesmo.")


# ---------- texto da conversa (90 dias) ----------

def textos(banco) -> list[MensagemConversa]:
    return [o for o in banco.db.adicionados if isinstance(o, MensagemConversa)]


def test_texto_anonimo_sai_com_cpf_e_codigo_mascarados(banco):
    mensagem(banco)
    cpf = f"{CPF_ANA[:3]}.{CPF_ANA[3:6]}.{CPF_ANA[6:9]}-{CPF_ANA[9:]}"

    assistente_conta.guardar_texto(banco.db, CANAL, TELEFONE, f"meu cpf é {cpf} e o código 482913", "Obrigado!",
                                   AGORA)

    cliente, resposta = textos(banco)
    assert (cliente.papel, cliente.texto) == ("cliente", "meu cpf é [CPF] e o código [código]")
    assert (resposta.papel, resposta.texto) == ("assistente", "Obrigado!")
    assert cliente.id_cliente is None and cliente.id_cliente_sem_conta is None


def test_cpf_sem_pontos_tambem_sai_mascarado(banco):
    mensagem(banco)
    assistente_conta.guardar_texto(banco.db, CANAL, TELEFONE, CPF_ANA, None, AGORA)
    assert [t.texto for t in textos(banco)] == ["[CPF]"]


def test_sem_resposta_guarda_so_a_mensagem_do_cliente(banco):
    mensagem(banco)
    assistente_conta.guardar_texto(banco.db, CANAL, TELEFONE, "oi", None, AGORA)
    assert [t.papel for t in textos(banco)] == ["cliente"]


def test_texto_com_a_conta_confirmada_fica_na_conta(banco):
    confirmar(banco)

    assistente_conta.guardar_texto(banco.db, CANAL, TELEFONE, "quantos pontos tenho?", "Você tem 1.220.", AGORA)

    assert {t.id_cliente for t in textos(banco)} == {ANA.id_usuario}


def test_confirmar_passa_o_texto_da_conversa_para_a_conta(banco):
    confirmar(banco)
    assert banco.texto_para_a_conta == [(AGORA, ANA.id_usuario, None)]


def test_cliente_sem_conta_tambem_recebe_o_texto(banco):
    confirmar(banco, cpf=CPF_SEM_CONTA)
    assert banco.texto_para_a_conta == [(AGORA, None, SEM_CONTA.id_cliente_sem_conta)]


def test_conversa_nova_comeca_o_texto_de_novo(banco):
    mensagem(banco)
    mensagem(banco, AGORA + timedelta(minutes=10))
    assert banco.conversas[(CANAL, TELEFONE)].conversa_iniciada_em == AGORA

    mensagem(banco, AGORA + timedelta(hours=1))
    assert banco.conversas[(CANAL, TELEFONE)].conversa_iniciada_em == AGORA + timedelta(hours=1)


def test_guardar_texto_sem_a_conversa_registrada(banco):
    with pytest.raises(RecursoNaoEncontrado):
        assistente_conta.guardar_texto(banco.db, CANAL, TELEFONE, "oi", None, AGORA)


def test_rota_do_texto_da_conversa(cliente, banco):
    mensagem(banco)

    resposta = cliente.post("/assistente/conversa/mensagens",
                            json={"canal": CANAL, "id_externo": TELEFONE, "cliente": "oi", "assistente": "Olá!"},
                            headers={"X-Assistente-Chave": CHAVE})

    assert resposta.status_code == 204
    assert [t.texto for t in textos(banco)] == ["oi", "Olá!"]


def test_rota_do_texto_recusa_mensagem_vazia(cliente, banco):
    mensagem(banco)
    resposta = cliente.post("/assistente/conversa/mensagens",
                            json={"canal": CANAL, "id_externo": TELEFONE, "cliente": "   "},
                            headers={"X-Assistente-Chave": CHAVE})
    assert resposta.status_code == 422


# ---------- lembrete de pontos ----------

def test_oferece_o_lembrete_uma_vez_por_conta(banco, pontos):
    confirmar(banco)

    primeira = assistente_conta.meus_pontos(banco.db, CANAL, TELEFONE, AGORA)
    segunda = assistente_conta.meus_pontos(banco.db, CANAL, TELEFONE, AGORA)

    assert (primeira["oferecer_lembrete"], segunda["oferecer_lembrete"]) == (True, False)
    assert ANA.lembrete_pontos_oferecido_em == AGORA


def test_nao_oferece_a_quem_ja_aceitou(banco, pontos):
    ANA.lembrete_pontos_aceito_em = AGORA - timedelta(days=30)
    confirmar(banco)
    assert assistente_conta.meus_pontos(banco.db, CANAL, TELEFONE, AGORA)["oferecer_lembrete"] is False


def test_nao_oferece_a_quem_nao_tem_pontos(banco, pontos):
    pontos.saldos, pontos.sem_data = [], {}
    confirmar(banco)
    assert assistente_conta.meus_pontos(banco.db, CANAL, TELEFONE, AGORA)["oferecer_lembrete"] is False
    assert ANA.lembrete_pontos_oferecido_em is None


def test_cliente_sem_conta_nao_recebe_o_lembrete(banco, pontos):
    confirmar(banco, cpf=CPF_SEM_CONTA)

    assert assistente_conta.meus_pontos(banco.db, CANAL, TELEFONE, AGORA)["oferecer_lembrete"] is False
    with pytest.raises(RegraDeNegocio, match="/cadastro"):
        assistente_conta.responder_lembrete(banco.db, CANAL, TELEFONE, True, AGORA)


def test_lembrete_exige_a_conta_confirmada(banco):
    mensagem(banco)
    with pytest.raises(NaoAutenticado):
        assistente_conta.responder_lembrete(banco.db, CANAL, TELEFONE, True, AGORA)


def test_aceitar_e_depois_recusar_o_lembrete(banco):
    confirmar(banco)

    aceitou = assistente_conta.responder_lembrete(banco.db, CANAL, TELEFONE, True, AGORA)
    assert aceitou["aceita"] is True and "por e-mail" in aceitou["mensagem"]
    assert (ANA.lembrete_pontos_aceito_em, ANA.lembrete_pontos_oferecido_em) == (AGORA, AGORA)

    recusou = assistente_conta.responder_lembrete(banco.db, CANAL, TELEFONE, False, AGORA + timedelta(minutes=5))
    assert recusou["aceita"] is False
    assert ANA.lembrete_pontos_aceito_em is None
    assert ANA.lembrete_pontos_oferecido_em == AGORA


def test_aceitar_de_novo_mantem_a_data_do_primeiro_aceite(banco):
    confirmar(banco)
    assistente_conta.responder_lembrete(banco.db, CANAL, TELEFONE, True, AGORA)
    assistente_conta.responder_lembrete(banco.db, CANAL, TELEFONE, True, AGORA + timedelta(minutes=5))
    assert ANA.lembrete_pontos_aceito_em == AGORA


def test_rota_do_lembrete(cliente, banco):
    confirmar(banco, momento=datetime.now(BRASILIA))

    resposta = cliente.put("/assistente/lembrete-pontos", json={"canal": CANAL, "id_externo": TELEFONE, "aceita": True},
                           headers={"X-Assistente-Chave": CHAVE})

    assert resposta.status_code == 200
    assert resposta.json()["aceita"] is True
    assert ANA.lembrete_pontos_aceito_em is not None
    assert "pontos" in resumo_atual(banco).assuntos


# ---------- aviso de pontos vencendo ----------

def lote(id_movimento, marca, restante, vence_em, valor_ponto="0.01"):
    return LotePontos(id_movimento=id_movimento, id_cliente=ANA.id_usuario, marca=marca, restante=restante,
                      pontos=restante, valor_ponto=Decimal(valor_ponto), vence_em=vence_em)


@pytest.fixture
def avisos(banco, monkeypatch):
    estado = SimpleNamespace(lotes=[], periodo=None)

    def lotes_para_avisar(db, desde, ate):
        estado.periodo = (desde, ate)
        return [(l, ANA) for l in estado.lotes]

    monkeypatch.setattr(assistente_repository, "lotes_para_avisar", lotes_para_avisar)
    monkeypatch.setattr(settings, "API_URL", "https://api.exemplo")
    return estado


def avisados(banco) -> list[int]:
    return [o.id_movimento for o in banco.db.adicionados if isinstance(o, AvisoPontosVencendo)]


def test_aviso_procura_os_lotes_que_vencem_nos_proximos_14_dias(banco, avisos):
    assistente_conta.avisar_pontos_vencendo(banco.db, AGORA)
    assert avisos.periodo == (AGORA, AGORA + timedelta(days=14))


def test_um_email_por_cliente_somando_por_marca_e_data(banco, avisos):
    dia = datetime(2026, 11, 5, 3, 0, tzinfo=BRASILIA)
    avisos.lotes = [lote(1, "vulto", 200, dia), lote(2, "vulto", 120, dia),
                    lote(3, "lorenzi", 50, dia + timedelta(days=3), "0.02")]

    resposta = assistente_conta.avisar_pontos_vencendo(banco.db, AGORA)

    assert resposta == {"clientes_avisados": 1, "clientes_com_falha": 0}
    [(para, texto)] = banco.emails
    assert para == ANA.email
    assert texto.startswith("Olá, Ana!")
    assert "- Vulto: 320 pontos (R$ 3,20) vencem em 05/11/2026.\n- Lorenzi: 50 pontos (R$ 1,00) vencem em 08/11/2026." in texto
    assert avisados(banco) == [1, 2, 3]


def test_email_leva_o_link_para_parar(banco, avisos):
    avisos.lotes = [lote(1, "vulto", 10, AGORA + timedelta(days=2))]

    assistente_conta.avisar_pontos_vencendo(banco.db, AGORA)

    link = re.search(r"https://api\.exemplo/lembrete-pontos/parar\?token=(\S+)", banco.emails[0][1])
    assert link and link.group(1).startswith(str(ANA.id_usuario))


def test_email_que_falha_nao_marca_os_lotes(banco, avisos):
    avisos.lotes = [lote(1, "vulto", 10, AGORA + timedelta(days=2))]
    banco.erro_email = ErroEmail("fora do ar")

    resposta = assistente_conta.avisar_pontos_vencendo(banco.db, AGORA)

    assert resposta == {"clientes_avisados": 0, "clientes_com_falha": 1}
    assert avisados(banco) == []


def test_sem_lotes_nao_manda_nada(banco, avisos):
    assert assistente_conta.avisar_pontos_vencendo(banco.db, AGORA) == {"clientes_avisados": 0,
                                                                         "clientes_com_falha": 0}
    assert banco.emails == []


def test_link_assinado_desliga_o_lembrete(banco, avisos):
    ANA.lembrete_pontos_aceito_em = AGORA
    token = assistente_conta.link_para_parar_o_lembrete(ANA.id_usuario).split("token=")[1]

    assert assistente_conta.parar_lembrete_pelo_link(banco.db, token) is True
    assert ANA.lembrete_pontos_aceito_em is None


@pytest.mark.parametrize("token", ["", "nao-e-uuid.abc", f"{ANA.id_usuario}.assinatura-errada",
                                   f"{uuid.uuid4()}.qualquer"])
def test_link_adulterado_nao_desliga(banco, avisos, token):
    ANA.lembrete_pontos_aceito_em = AGORA
    assert assistente_conta.parar_lembrete_pelo_link(banco.db, token) is False
    assert ANA.lembrete_pontos_aceito_em == AGORA


def test_abrir_o_link_so_mostra_o_botao_e_o_post_desliga(cliente, banco, avisos):
    ANA.lembrete_pontos_aceito_em = AGORA
    token = assistente_conta.link_para_parar_o_lembrete(ANA.id_usuario).split("token=")[1]

    pagina = cliente.get("/lembrete-pontos/parar", params={"token": token})
    assert pagina.status_code == 200 and "Parar de receber" in pagina.text
    assert ANA.lembrete_pontos_aceito_em == AGORA

    resposta = cliente.post("/lembrete-pontos/parar", params={"token": token})
    assert resposta.status_code == 200 and "não vai mais receber" in resposta.text
    assert ANA.lembrete_pontos_aceito_em is None


def test_post_com_link_invalido_responde_400(cliente, banco, avisos):
    resposta = cliente.post("/lembrete-pontos/parar", params={"token": "abc"})
    assert resposta.status_code == 400


def test_rota_do_aviso_exige_a_chave(cliente, banco, avisos):
    assert cliente.post("/assistente/avisos/pontos-vencendo").status_code == 401
    resposta = cliente.post("/assistente/avisos/pontos-vencendo", headers={"X-Assistente-Chave": CHAVE})
    assert resposta.status_code == 200
    assert resposta.json() == {"clientes_avisados": 0, "clientes_com_falha": 0}
