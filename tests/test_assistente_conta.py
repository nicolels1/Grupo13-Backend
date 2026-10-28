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
from src.models.assistente import AfirmacaoAssistente, CodigoVerificacao, ConversaCanal
from src.models.contas import ClienteSemConta, Usuario
from src.models.estoque import Unidade
from src.models.fidelidade import RegraFidelidade
from src.models.vendas import Pedido
from src.repositories import assistente_repository, cliente_sem_conta_repository, fidelidade_repository
from src.repositories import pedido_repository, usuario_repository
from src.use_cases import assistente_conta
from src.use_cases.erros import MuitasTentativas, NaoAutenticado, RecursoNaoEncontrado, RegraDeNegocio
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
    estado = SimpleNamespace(conversas={}, emails=[], erro_email=None, db=SessaoFalsa())

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
    m(usuario_repository, "buscar_cliente_por_cpf", lambda db, cpf: ANA if cpf == CPF_ANA else None)
    m(usuario_repository, "buscar_por_id", lambda db, id_usuario: ANA)
    m(cliente_sem_conta_repository, "buscar_por_cpf", lambda db, cpf: SEM_CONTA if cpf == CPF_SEM_CONTA else None)
    m(cliente_sem_conta_repository, "buscar", lambda db, id_cliente_sem_conta: SEM_CONTA)
    m(assistente_conta, "enviar_email", email)
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
                      "avisar_limite": False, "bloqueado_ate": None}
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


def test_rota_recusa_canal_desconhecido(cliente):
    resposta = cliente.post("/assistente/conversa", json={"canal": "telegram", "id_externo": TELEFONE},
                            headers={"X-Assistente-Chave": CHAVE})
    assert resposta.status_code == 422


def test_rota_de_pontos_sem_confirmar_responde_401(cliente):
    cabecalho = {"X-Assistente-Chave": CHAVE}
    cliente.post("/assistente/conversa", json={"canal": CANAL, "id_externo": TELEFONE}, headers=cabecalho)

    resposta = cliente.post("/assistente/meus-pontos", json={"canal": CANAL, "id_externo": TELEFONE}, headers=cabecalho)

    assert resposta.status_code == 401
