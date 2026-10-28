import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.app import app
from src.database.session import get_db
from src.dependencies.permissoes import get_usuario_ativo
from src.repositories import assistente_interno_repository, resumo_repository, unidade_repository
from src.use_cases import assistente_interno, ferramentas_internas
from src.use_cases.conferencia_numeros import numeros_conferem, numeros_sem_origem
from src.use_cases.erros import RegraDeNegocio, RecursoNaoEncontrado, ServicoIndisponivel, SemPermissao
from src.use_cases.ferramentas_internas import datas_do_periodo, reais
from src.utils import modelo_ia
from src.utils.modelo_ia import ChamadaFerramenta, RespostaModelo

USUARIO = SimpleNamespace(id_usuario=uuid.UUID("11111111-1111-1111-1111-111111111111"), tipo_conta="interna",
                          id_modelo_acesso=2)
QUARTA = date(2026, 10, 28)
SAVASSI = SimpleNamespace(id_unidade=5, nome="Vulto Savassi", marca="vulto", tipo="loja", cidade="Belo Horizonte",
                          uf="MG")


# ---------- períodos: o backend converte o nome em datas ----------

@pytest.mark.parametrize("periodo, de, ate", [
    ("hoje", date(2026, 10, 28), date(2026, 10, 28)),
    ("ontem", date(2026, 10, 27), date(2026, 10, 27)),
    ("esta_semana", date(2026, 10, 26), date(2026, 10, 28)),
    ("semana_passada", date(2026, 10, 19), date(2026, 10, 25)),
    ("este_mes", date(2026, 10, 1), date(2026, 10, 28)),
    ("mes_passado", date(2026, 9, 1), date(2026, 9, 30)),
    ("ultimos_7_dias", date(2026, 10, 22), date(2026, 10, 28)),
    ("ultimos_30_dias", date(2026, 9, 29), date(2026, 10, 28)),
])
def test_periodo_vira_datas_com_semana_de_segunda_a_domingo(periodo, de, ate):
    assert datas_do_periodo(periodo, QUARTA) == (de, ate)


def test_mes_passado_em_janeiro_e_dezembro():
    assert datas_do_periodo("mes_passado", date(2027, 1, 15)) == (date(2026, 12, 1), date(2026, 12, 31))


@pytest.mark.parametrize("de, ate", [
    (None, "2026-10-10"), ("2026-10-10", "2026-10-01"), ("2026-10-01", "2026-10-29"), ("2025-01-01", "2026-10-01"),
    ("01/10/2026", "2026-10-10"),
])
def test_intervalo_invalido(de, ate):
    with pytest.raises(RegraDeNegocio):
        datas_do_periodo("intervalo", QUARTA, de, ate)


def test_intervalo_valido():
    assert datas_do_periodo("intervalo", QUARTA, "2026-09-10", "2026-09-20") == (date(2026, 9, 10), date(2026, 9, 20))


# ---------- vendas_do_periodo ----------

@pytest.fixture
def vendas(monkeypatch):
    estado = SimpleNamespace(pode_vender=True, marcas={"lorenzi", "vulto"}, consultas=[])
    linhas = [
        {"mes": date(2026, 9, 1), "id_unidade": 5, "unidade": "Vulto Savassi", "canal_venda": "loja", "pedidos": 10,
         "valor": Decimal("1500.00"), "frete": Decimal("0")},
        {"mes": date(2026, 10, 1), "id_unidade": 5, "unidade": "Vulto Savassi", "canal_venda": "site", "pedidos": 2,
         "valor": Decimal("300.50"), "frete": Decimal("40.00")},
        {"mes": date(2026, 10, 1), "id_unidade": 7, "unidade": "Vulto Pinheiros", "canal_venda": "loja", "pedidos": 30,
         "valor": Decimal("48230.00"), "frete": Decimal("0")},
    ]

    def faturamento(db, desde, ate, id_unidade, marca):
        estado.consultas.append((desde, ate, id_unidade, marca))
        return [l for l in linhas if id_unidade is None or l["id_unidade"] == id_unidade]

    m = monkeypatch.setattr
    m(resumo_repository, "faturamento_por_mes", faturamento)
    m(ferramentas_internas, "usuario_tem_permissao", lambda db, u, *codigos: estado.pode_vender)
    m(ferramentas_internas, "marcas_visiveis", lambda db, u: estado.marcas)
    m(unidade_repository, "buscar_unidade", lambda db, i: SAVASSI if i == 5 else None)
    return estado


def test_vendas_somam_canais_e_unidades_com_o_frete_a_parte(vendas):
    r = ferramentas_internas.vendas_do_periodo(None, USUARIO, {"periodo": "ultimos_30_dias"}, QUARTA)

    assert r["faturamento"] == "R$ 50.030,50"
    assert r["pedidos"] == "42"
    assert r["ticket_medio"] == "R$ 1.191,20"
    assert r["frete_a_parte"] == "R$ 40,00"
    assert r["onde"] == "na rede inteira"
    assert r["periodo"]["de"] == "29/09/2026" and r["periodo"]["ate"] == "28/10/2026"
    assert [u["unidade"] for u in r["por_unidade"]] == ["Vulto Pinheiros", "Vulto Savassi"]
    assert r["texto"].startswith("Vendas na rede inteira de 29/09/2026 a 28/10/2026: R$ 50.030,50 em 42 pedidos")


def test_periodo_vai_do_comeco_do_primeiro_dia_ate_a_meia_noite_depois_do_ultimo(vendas):
    ferramentas_internas.vendas_do_periodo(None, USUARIO, {"periodo": "ontem"}, QUARTA)

    desde, ate, _, _ = vendas.consultas[0]
    assert (desde.date(), desde.hour, ate.date(), ate.hour) == (date(2026, 10, 27), 0, date(2026, 10, 28), 0)


def test_vendas_de_uma_unidade_e_um_canal(vendas):
    r = ferramentas_internas.vendas_do_periodo(
        None, USUARIO, {"periodo": "este_mes", "id_unidade": 5, "canal": "site"}, QUARTA
    )

    assert (r["faturamento"], r["pedidos"], r["canal"]) == ("R$ 300,50", "2", "site")
    assert r["onde"] == "na unidade Vulto Savassi"


def test_sem_venda_no_periodo(vendas):
    r = ferramentas_internas.vendas_do_periodo(None, USUARIO, {"periodo": "hoje", "canal": "app"}, QUARTA)

    assert (r["faturamento"], r["pedidos"], r["ticket_medio"]) == ("R$ 0,00", "0", None)


def test_vendas_pedem_permissao_de_vendas(vendas):
    vendas.pode_vender = False
    with pytest.raises(SemPermissao):
        ferramentas_internas.vendas_do_periodo(None, USUARIO, {"periodo": "hoje"}, QUARTA)


def test_unidade_ou_marca_que_a_pessoa_nao_ve(vendas):
    vendas.marcas = {"lorenzi"}
    with pytest.raises(SemPermissao):
        ferramentas_internas.vendas_do_periodo(None, USUARIO, {"periodo": "hoje", "id_unidade": 5}, QUARTA)
    with pytest.raises(SemPermissao):
        ferramentas_internas.vendas_do_periodo(None, USUARIO, {"periodo": "hoje", "marca": "vulto"}, QUARTA)


def test_quem_ve_uma_marca_so_recebe_essa(vendas):
    vendas.marcas = {"vulto"}
    r = ferramentas_internas.vendas_do_periodo(None, USUARIO, {"periodo": "hoje"}, QUARTA)

    assert vendas.consultas[0][3] == "vulto"
    assert r["onde"] == "nas unidades da Vulto"


def test_unidade_inexistente(vendas):
    with pytest.raises(RecursoNaoEncontrado):
        ferramentas_internas.vendas_do_periodo(None, USUARIO, {"periodo": "hoje", "id_unidade": 99}, QUARTA)


def test_reais_no_formato_brasileiro():
    assert reais(Decimal("1234567.8")) == "R$ 1.234.567,80"


# ---------- conferência dos números ----------

RESULTADO = {"faturamento": "R$ 48.230,00", "pedidos": "112", "periodo": {"de": "20/10/2026", "ate": "26/10/2026"}}


def test_numeros_que_vieram_da_ferramenta_passam():
    texto = "A Savassi vendeu R$ 48.230,00 em 112 pedidos de 20/10/2026 a 26/10/2026."
    assert numeros_conferem(texto, [RESULTADO], "quanto vendeu?")


@pytest.mark.parametrize("texto", [
    "Vendeu R$ 48.230,01.",            # um centavo diferente
    "Vendeu R$ 48 mil.",               # arredondado
    "Foram 112 pedidos, 15% a mais.",  # conta feita pelo modelo
])
def test_numero_sem_origem_nao_passa(texto):
    assert not numeros_conferem(texto, [RESULTADO], "quanto vendeu?")


def test_data_valida_e_numero_das_instrucoes_passam():
    texto = "Não faço previsão. Amanhã é 29/10/2026; posso ver vendas de 01/01/2025 a 31/12/2025 na unidade 45."
    assert numeros_conferem(texto, [], "quanto vamos vender amanhã?", "Hoje é quarta, 28/10/2026.\n- 45: Vulto")


def test_data_curta_passa():
    assert numeros_conferem("Posso consultar de 01/01 a 30/06 e de 1/7 a 31/12.", [], "e em 2025?")


def test_data_que_nao_existe_e_conferida_como_numero():
    assert numeros_sem_origem("Vendeu em 32/13/2026.", [], "quando?") == {"32", "13", "2026"}
    assert numeros_sem_origem("Foram 45/60 pedidos.", [], "quantos?") == {"45", "60"}


def test_data_nao_libera_valor_inventado():
    assert numeros_sem_origem("Em 29/10/2026 vamos vender R$ 1.500,00.", [], "amanhã?") == {"1.500,00"}


def test_digito_sozinho_e_numero_da_pergunta_passam():
    assert numeros_conferem("Nos últimos 30 dias, as 2 lojas...", [], "e nos últimos 30 dias?")


# ---------- a conversa: modelo falso, banco falso ----------

class BancoFalso:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def rodada(texto="", chamadas=(), parou_por=None, tokens=(100, 20, 1000, 0)):
    entrada, saida, lidos, escritos = tokens
    return RespostaModelo(conteudo=[{"type": "text", "text": texto}], texto=texto, chamadas=list(chamadas),
                          parou_por=parou_por or ("tool_use" if chamadas else "end_turn"), modelo="claude-sonnet-5-5",
                          tokens_entrada=entrada, tokens_saida=saida, tokens_cache_lidos=lidos,
                          tokens_cache_escritos=escritos)


VENDAS = ChamadaFerramenta("t1", "vendas_do_periodo", {"periodo": "semana_passada", "id_unidade": 5})


@pytest.fixture
def conversa(monkeypatch):
    agora = datetime(2026, 10, 28, 15, 0, tzinfo=timezone.utc)
    estado = SimpleNamespace(rodadas=[], recebido=[], mensagens=[], conversas={}, ferramenta=None, agora=agora)

    def criar(db, id_usuario, momento):
        c = SimpleNamespace(id_conversa=len(estado.conversas) + 1, id_usuario=id_usuario, ultima_msg_em=momento,
                            iniciada_em=momento, avaliacao=None)
        estado.conversas[c.id_conversa] = c
        return c

    def adicionar(db, c, **campos):
        estado.mensagens.append({"id_conversa": c.id_conversa, **campos})

    def conversar(instrucoes, mensagens, ferramentas):
        estado.recebido.append([dict(m) for m in mensagens])
        if not estado.rodadas:
            raise AssertionError("o modelo foi chamado mais vezes que o esperado")
        proxima = estado.rodadas.pop(0)
        if isinstance(proxima, Exception):
            raise proxima
        return proxima

    def executar(db, usuario, parametros, hoje):
        if estado.ferramenta is not None:
            return estado.ferramenta(parametros)
        return {"faturamento": "R$ 48.230,00", "pedidos": "112", "texto": "Vendas na unidade Vulto Savassi: "
                "R$ 48.230,00 em 112 pedidos.", "links": [{"rotulo": "Abrir a Visão geral", "url": "/interno"}]}

    m = monkeypatch.setattr
    m(assistente_interno, "_agora", lambda: estado.agora)
    m(assistente_interno, "_instrucoes", lambda db, usuario, hoje: "instruções")
    m(assistente_interno_repository, "buscar_conversa", lambda db, i: estado.conversas.get(i))
    m(assistente_interno_repository, "criar_conversa", criar)
    m(assistente_interno_repository, "adicionar_mensagem", adicionar)
    m(assistente_interno_repository, "memoria", lambda db, i: [
        SimpleNamespace(papel=msg["papel"], texto=msg["texto"]) for msg in estado.mensagens
        if msg["id_conversa"] == i and msg["papel"] in ("usuario", "assistente")
    ])
    m(modelo_ia, "conversar", conversar)
    m(ferramentas_internas.VENDAS_DO_PERIODO, "executar", executar)
    return estado


def papeis(estado):
    return [m["papel"] for m in estado.mensagens]


def test_pergunta_vira_ferramenta_e_resposta_com_o_numero_do_banco(conversa):
    conversa.rodadas = [rodada(chamadas=[VENDAS]), rodada("A Savassi vendeu R$ 48.230,00 em 112 pedidos.")]
    db = BancoFalso()

    r = assistente_interno.responder(db, USUARIO, "quanto a savassi vendeu semana passada?")

    assert r["resposta"] == "A Savassi vendeu R$ 48.230,00 em 112 pedidos."
    assert r["ferramentas"] == ["vendas_do_periodo"]
    assert r["numeros_conferidos"] is True
    assert r["links"] == [{"rotulo": "Abrir a Visão geral", "url": "/interno"}]
    assert papeis(conversa) == ["usuario", "ferramenta", "assistente"]
    ferramenta, resposta = conversa.mensagens[1], conversa.mensagens[2]
    assert (ferramenta["ferramenta"], ferramenta["parametros"], ferramenta["resultado"]) == (
        "vendas_do_periodo", {"periodo": "semana_passada", "id_unidade": 5}, "dado")
    # tokens das duas rodadas somados na resposta, para o custo real
    assert (resposta["tokens_entrada"], resposta["tokens_saida"], resposta["tokens_cache_lidos"]) == (200, 40, 2000)
    assert resposta["modelo"] == "claude-sonnet-5-5"
    # a pergunta é gravada antes do modelo, e a resposta no fim
    assert db.commits == 2


def test_resultado_da_ferramenta_volta_ao_modelo_sem_os_links(conversa):
    conversa.rodadas = [rodada(chamadas=[VENDAS]), rodada("R$ 48.230,00")]

    assistente_interno.responder(BancoFalso(), USUARIO, "vendas?")

    resultado = conversa.recebido[1][-1]["content"][0]
    assert resultado["type"] == "tool_result" and resultado["tool_use_id"] == "t1"
    assert "48.230,00" in resultado["content"] and "links" not in resultado["content"]


def test_numero_inventado_troca_pela_resposta_fixa_da_ferramenta(conversa, caplog):
    conversa.rodadas = [rodada(chamadas=[VENDAS]), rodada("A cliente Bruna comprou: R$ 50.000,00 em 112 pedidos.")]

    r = assistente_interno.responder(BancoFalso(), USUARIO, "vendas?")

    assert r["resposta"] == "Vendas na unidade Vulto Savassi: R$ 48.230,00 em 112 pedidos."
    assert r["numeros_conferidos"] is False
    assert conversa.mensagens[-1]["numeros_conferidos"] is False
    # o log diz só o número barrado, nunca o texto da resposta
    assert "50.000,00" in caplog.text and "Bruna" not in caplog.text


def test_numero_sem_ferramenta_nenhuma_nao_chega_a_pessoa(conversa):
    conversa.rodadas = [rodada("A rede vendeu R$ 1.000.000,00 no ano.")]

    r = assistente_interno.responder(BancoFalso(), USUARIO, "quanto vendeu no ano?")

    assert r["resposta"] == assistente_interno.SEM_NUMERO_CONFIRMADO


def test_sem_permissao_volta_ao_modelo_como_erro(conversa):
    def recusa(parametros):
        raise SemPermissao("Sem permissão para ver vendas")

    conversa.ferramenta = recusa
    conversa.rodadas = [rodada(chamadas=[VENDAS]), rodada("Você não tem acesso às vendas.")]

    r = assistente_interno.responder(BancoFalso(), USUARIO, "vendas?")

    resultado = conversa.recebido[1][-1]["content"][0]
    assert resultado["is_error"] is True and "sem_permissao" in resultado["content"]
    assert conversa.mensagens[1]["resultado"] == "sem_permissao"
    assert r["resposta"] == "Você não tem acesso às vendas." and r["links"] == []


def test_recusa_com_numero_inventado_responde_o_motivo_da_recusa(conversa):
    def recusa(parametros):
        raise SemPermissao("Sem permissão para ver vendas")

    conversa.ferramenta = recusa
    conversa.rodadas = [rodada(chamadas=[VENDAS]), rodada("Você não tem acesso, mas foram uns R$ 900,00.")]

    r = assistente_interno.responder(BancoFalso(), USUARIO, "e só no site?")

    assert r["resposta"] == "Sem permissão para ver vendas."
    assert r["numeros_conferidos"] is False


def test_parametro_invalido_volta_ao_modelo_para_corrigir(conversa):
    def invalido(parametros):
        raise RegraDeNegocio("O período não pode terminar no futuro")

    conversa.ferramenta = invalido
    conversa.rodadas = [rodada(chamadas=[VENDAS]), rodada("Esse período ainda não terminou.")]

    assistente_interno.responder(BancoFalso(), USUARIO, "vendas de amanhã?")

    assert conversa.mensagens[1]["resultado"] == "invalido"


def test_erro_de_verdade_na_ferramenta_responde_sem_passar_pelo_modelo(conversa):
    def quebra(parametros):
        raise RuntimeError("banco fora do ar")

    conversa.ferramenta = quebra
    conversa.rodadas = [rodada(chamadas=[VENDAS])]
    db = BancoFalso()

    r = assistente_interno.responder(db, USUARIO, "vendas?")

    assert r["resposta"] == assistente_interno.NAO_CONSULTOU
    assert len(conversa.recebido) == 1
    assert conversa.mensagens[1]["resultado"] == "falhou"
    assert db.rollbacks == 1


def test_modelo_fora_do_ar(conversa):
    conversa.rodadas = [modelo_ia.ErroModelo("Modelo sem resposta")]

    with pytest.raises(ServicoIndisponivel):
        assistente_interno.responder(BancoFalso(), USUARIO, "vendas?")
    assert papeis(conversa) == ["usuario", "assistente"]
    assert conversa.mensagens[-1]["texto"] == assistente_interno.FORA_DO_AR


def test_para_depois_de_quatro_rodadas_de_ferramenta(conversa):
    conversa.rodadas = [rodada(chamadas=[VENDAS]) for _ in range(5)]

    r = assistente_interno.responder(BancoFalso(), USUARIO, "vendas?")

    assert r["resposta"] == assistente_interno.NAO_TERMINOU
    assert papeis(conversa).count("ferramenta") == 4


def test_parametros_cortados_nao_sao_executados(conversa):
    conversa.rodadas = [rodada(chamadas=[VENDAS], parou_por="max_tokens")]

    r = assistente_interno.responder(BancoFalso(), USUARIO, "vendas?")

    assert "ferramenta" not in papeis(conversa)
    assert r["resposta"] == assistente_interno.NAO_TERMINOU


def test_recusa_do_modelo(conversa):
    conversa.rodadas = [rodada(parou_por="refusal")]

    assert assistente_interno.responder(BancoFalso(), USUARIO, "...")["resposta"] == assistente_interno.RECUSA


def test_conversa_continua_com_a_memoria_e_termina_depois_de_30_minutos(conversa):
    conversa.rodadas = [rodada("Qual loja?"), rodada("Ok, a Savassi.")]
    primeira = assistente_interno.responder(BancoFalso(), USUARIO, "quanto vendeu?")

    conversa.agora += timedelta(minutes=10)
    segunda = assistente_interno.responder(BancoFalso(), USUARIO, "a savassi", primeira["id_conversa"])

    assert segunda["id_conversa"] == primeira["id_conversa"]
    assert [m["role"] for m in conversa.recebido[1]] == ["user", "assistant", "user"]
    assert conversa.recebido[1][1]["content"] == "Qual loja?"

    conversa.rodadas = [rodada("Oi!")]
    conversa.agora += timedelta(minutes=31)
    terceira = assistente_interno.responder(BancoFalso(), USUARIO, "oi", primeira["id_conversa"])
    assert terceira["id_conversa"] != primeira["id_conversa"]
    assert len(conversa.recebido[2]) == 1


def test_conversa_de_outra_pessoa(conversa):
    conversa.conversas[9] = SimpleNamespace(id_conversa=9, id_usuario=uuid.uuid4(), ultima_msg_em=conversa.agora)

    with pytest.raises(RecursoNaoEncontrado):
        assistente_interno.responder(BancoFalso(), USUARIO, "oi", 9)


def test_cliente_nao_usa_o_assistente_interno(conversa):
    cliente = SimpleNamespace(id_usuario=uuid.uuid4(), tipo_conta="cliente", id_modelo_acesso=None)

    with pytest.raises(SemPermissao):
        assistente_interno.responder(BancoFalso(), cliente, "vendas?")


def test_instrucoes_levam_hoje_e_so_as_unidades_que_a_pessoa_ve(monkeypatch):
    unidades = {"vulto": [SAVASSI], "lorenzi": [SimpleNamespace(id_unidade=1, nome="Lorenzi Batel", marca="lorenzi",
                                                                tipo="loja")]}
    monkeypatch.setattr(assistente_interno, "marcas_visiveis", lambda db, u: {"vulto"})
    monkeypatch.setattr(resumo_repository, "unidades_ativas", lambda db, marca: unidades[marca])

    texto = assistente_interno._instrucoes(None, USUARIO, QUARTA)

    assert "Hoje é quarta, 28/10/2026" in texto
    assert "- 5: Vulto Savassi, Vulto, loja, Belo Horizonte/MG" in texto
    assert "Batel" not in texto


# ---------- a rota ----------

def test_rota_usa_o_login_e_nao_a_chave_do_n8n(monkeypatch):
    app.dependency_overrides[get_usuario_ativo] = lambda: USUARIO
    app.dependency_overrides[get_db] = lambda: None
    monkeypatch.setattr(assistente_interno, "responder", lambda db, u, texto, id_conversa: {
        "id_conversa": 1, "resposta": f"eco: {texto}", "links": [], "ferramentas": [], "numeros_conferidos": None})
    try:
        cliente = TestClient(app)
        resposta = cliente.post("/assistente/interno/mensagens", json={"texto": "  oi  "})
        vazia = cliente.post("/assistente/interno/mensagens", json={"texto": "   "})
    finally:
        app.dependency_overrides.clear()

    assert resposta.status_code == 200 and resposta.json()["resposta"] == "eco: oi"
    assert vazia.status_code == 422
