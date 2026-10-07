from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.models.catalogo import ImagemProduto
from src.repositories import catalogo_repository, pedido_repository, permissao_repository, unidade_repository
from src.repositories import resumo_repository as repo
from src.use_cases import resumo, vendas
from src.use_cases.erros import RecursoNaoEncontrado, SemPermissao
from tests.apoio import SessaoFalsa, api, funcionario  # noqa: F401

HOJE = date(2026, 10, 7)
LOJA, CD = 1, 2


@pytest.fixture
def banco(monkeypatch):
    """Consultas do resumo trocadas por respostas prontas; `permissoes` decide o que a conta vê."""
    estado = SimpleNamespace(permissoes=set(), admin=False, unidade_filtrada=[])
    m = monkeypatch.setattr
    m(resumo, "_hoje", lambda: HOJE)
    m(resumo, "usuario_tem_permissao", lambda db, u, *codigos: estado.admin or bool(set(codigos) & estado.permissoes))
    m(permissao_repository, "modelo_eh_admin", lambda db, i: estado.admin)
    m(unidade_repository, "buscar_unidade", lambda db, i: object() if i in (LOJA, CD) else None)

    def vendas_por_dia(db, desde, id_unidade):
        estado.unidade_filtrada.append(id_unidade)
        return [{"dia": HOJE, "canal": "online", "pedidos": 2, "valor": Decimal("300.00")},
                {"dia": HOJE - timedelta(days=13), "canal": "loja_fisica", "pedidos": 1, "valor": Decimal("50.00")}]

    m(repo, "vendas_por_dia", vendas_por_dia)
    m(repo, "mais_vendidas", lambda db, desde, u, limite: [
        {"id_variante": 10, "id_produto": 5, "produto": "Camisa", "cor": "Azul", "tamanho": "M", "sku": "CAM-AZ-M",
         "quantidade_vendida": 7}])
    m(repo, "saldos_das_variantes", lambda db, ids, u: {10: 12})
    m(catalogo_repository, "imagens_dos_produtos", lambda db, ids: {5: [
        ImagemProduto(id_produto=5, cor=None, caminho_arquivo="produto-5/geral.png", ordem=1),
        ImagemProduto(id_produto=5, cor="Azul", caminho_arquivo="produto-5/azul.png", ordem=2)]})
    m(repo, "chamados_por_status", lambda db, desde, u: {"aberto": 3, "concluido": 4})
    m(repo, "chamados_por_dia", lambda db, desde, u: ({HOJE: 2}, {HOJE - timedelta(days=1): 1}))
    m(repo, "vendas_por_canal", lambda db, desde, ate=None: (
        [{"canal": "online", "pedidos": 4, "valor": Decimal("400.00")}] if ate is None
        else [{"canal": "online", "pedidos": 2, "valor": Decimal("320.00")}]))
    m(repo, "ruptura_online", lambda db: (20, 3))
    m(repo, "primeira_resposta", lambda db, desde: (2.345, 1))
    m(repo, "avaliacoes", lambda db, desde: (4.25, 8, 2))
    m(repo, "saldo_total", lambda db, u=None: 600)
    m(repo, "pecas_vendidas_por_unidade", lambda db, desde: {LOJA: 30, CD: 30})
    m(repo, "retiradas_perto_de_vencer", lambda db, antes: {LOJA: 2})
    m(repo, "unidades_ativas", lambda db: [SimpleNamespace(id_unidade=CD, nome="CD", tipo="cd"),
                                           SimpleNamespace(id_unidade=LOJA, nome="Loja", tipo="loja")])
    m(repo, "vendas_por_unidade", lambda db, desde: {LOJA: (3, Decimal("90.00"))})
    m(repo, "saldo_por_unidade", lambda db: {LOJA: 50, CD: 550})
    m(repo, "abaixo_do_minimo_por_unidade", lambda db: {LOJA: 1})
    m(repo, "transferencias_por_unidade", lambda db: ({CD: 2}, {LOJA: 1}))
    return estado


def conta(**campos):
    return funcionario(**campos)


# ---------- regras de cálculo ----------

def test_ultimos_dias_comecam_a_meia_noite_de_brasilia():
    inicio = resumo.inicio_dos_ultimos(14, HOJE)
    assert inicio.date() == date(2026, 9, 24) and (inicio.hour, inicio.minute) == (0, 0)
    assert inicio.utcoffset() == timedelta(hours=-3)


@pytest.mark.parametrize("saldo, vendidas, esperado", [(600, 60, 300.0), (10, 3, 100.0), (50, 0, None)])
def test_cobertura_em_dias(saldo, vendidas, esperado):
    assert resumo.cobertura_dias(saldo, vendidas) == esperado


@pytest.mark.parametrize("atual, anterior, esperado", [(150, 100, 50.0), (Decimal("80"), Decimal("100"), -20.0),
                                                       (5, 0, None)])
def test_variacao_percentual(atual, anterior, esperado):
    assert resumo.variacao_pct(atual, anterior) == esperado


# ---------- quem vê o quê ----------

def test_cliente_nao_ve_resumo(banco):
    with pytest.raises(SemPermissao):
        resumo.resumo(SessaoFalsa(), conta(tipo_conta="cliente"), None)


def test_conta_sem_vendas_nem_atendimento_so_recebe_o_cabecalho(banco):
    dados = resumo.resumo(SessaoFalsa(), conta(), None)
    assert set(dados) == {"id_unidade", "gerado_em"}


def test_vendedor_ve_vendas_e_mais_vendidas(banco):
    banco.permissoes = {"registrar_venda_fisica"}
    dados = resumo.resumo(SessaoFalsa(), conta(), LOJA)
    assert set(dados) == {"id_unidade", "gerado_em", "vendas_por_dia", "mais_vendidas"}
    assert banco.unidade_filtrada == [LOJA]


def test_atendente_ve_so_chamados(banco):
    banco.permissoes = {"atender_chamado"}
    chamados = resumo.resumo(SessaoFalsa(), conta(), None)["chamados"]
    assert (chamados["abertos"], chamados["em_andamento"], chamados["concluidos_7_dias"]) == (3, 0, 4)
    assert len(chamados["por_dia"]) == 7 and chamados["por_dia"][-1] == {"dia": HOJE, "abertos": 2, "concluidos": 0}


def test_unidade_inexistente(banco):
    with pytest.raises(RecursoNaoEncontrado):
        resumo.resumo(SessaoFalsa(), conta(), 99)


# ---------- seções ----------

def test_vendas_por_dia_tem_os_14_dias_mesmo_sem_venda(banco):
    banco.permissoes = {"preparar_entregar_pedido"}
    dias = resumo.resumo(SessaoFalsa(), conta(), None)["vendas_por_dia"]
    assert [d["dia"] for d in dias] == [HOJE - timedelta(days=n) for n in range(13, -1, -1)]
    assert dias[0]["loja_fisica"] == {"pedidos": 1, "valor": Decimal("50.00")}
    assert dias[-1]["online"] == {"pedidos": 2, "valor": Decimal("300.00")}
    assert dias[5]["online"] == {"pedidos": 0, "valor": Decimal("0.00")}


def test_mais_vendida_usa_a_foto_da_cor(banco):
    banco.permissoes = {"registrar_venda_fisica"}
    [item] = resumo.resumo(SessaoFalsa(), conta(), None)["mais_vendidas"]
    assert item["foto_url"].endswith("produto-5/azul.png")
    assert (item["quantidade_vendida"], item["saldo_atual"]) == (7, 12)


def test_admin_ve_a_rede_agora(banco):
    banco.admin = True
    rede = resumo.resumo(SessaoFalsa(), conta(), LOJA)["rede_agora"]

    assert rede["cobertura_dias"] == 300.0  # 600 peças ÷ (60 vendidas em 30 dias ÷ 30)
    assert rede["ruptura_online_pct"] == 15.0  # 3 de 20 variantes à venda
    online = rede["vendas_14_dias"]["online"]
    assert (online["pedidos"], online["variacao_pedidos_pct"], online["variacao_valor_pct"]) == (4, 100.0, 25.0)
    assert rede["vendas_14_dias"]["loja_fisica"]["variacao_valor_pct"] is None  # sem base de comparação
    ticket = rede["ticket_medio_30_dias"]
    assert ticket == {"online": Decimal("100.00"), "loja_fisica": None, "total": Decimal("100.00")}
    assert (rede["primeira_resposta_mediana_horas"], rede["chamados_esperando_primeira_resposta"]) == (2.3, 1)
    assert rede["avaliacoes"] == {"nota_media_90_dias": 4.3, "quantidade_90_dias": 8, "denuncias_pendentes": 2}
    assert rede["retiradas_perto_de_vencer"] == 2


def test_admin_ve_uma_linha_por_unidade(banco):
    banco.admin = True
    cd, loja = resumo.resumo(SessaoFalsa(), conta(), None)["por_unidade"]

    assert loja["vendas_7_dias"] == {"pedidos": 3, "valor": Decimal("90.00")}
    assert loja["cobertura_dias"] == 50.0  # 50 peças ÷ (30 vendidas em 30 dias ÷ 30)
    assert (loja["variantes_abaixo_do_minimo"], loja["retiradas_perto_de_vencer"]) == (1, 2)
    assert (loja["transferencias_esperando_envio"], loja["transferencias_chegando"]) == (0, 1)
    assert cd["retiradas_perto_de_vencer"] is None and cd["vendas_7_dias"]["pedidos"] == 0
    assert cd["transferencias_esperando_envio"] == 2


# ---------- rota ----------

def test_rota_deixa_ausente_a_secao_que_a_conta_nao_ve(api, banco):
    banco.permissoes = {"atender_chamado"}
    # permitido=False: a conta não é Admin (o cliente de teste trata como Admin por padrão)
    corpo = api(SessaoFalsa(), usuario=conta(), permitido=False).get("/visao-geral/resumo").json()
    assert "chamados" in corpo and "vendas_por_dia" not in corpo and "rede_agora" not in corpo


def test_rota_mantem_null_dentro_das_secoes(api, banco):
    banco.admin = True
    banco.permissoes = set()
    corpo = api(SessaoFalsa(), usuario=conta()).get("/visao-geral/resumo").json()
    assert corpo["rede_agora"]["ticket_medio_30_dias"]["loja_fisica"] is None
    assert corpo["por_unidade"][0]["retiradas_perto_de_vencer"] is None


# ---------- filtro de data das vendas ----------

def test_vendas_de_hoje_filtram_pelo_dia_inteiro_de_brasilia(monkeypatch):
    recebidos = {}
    monkeypatch.setattr(pedido_repository, "listar_pedidos",
                        lambda db, limit, offset, **f: (recebidos.update(f), ([], 0))[1])
    vendas.listar(SessaoFalsa(), 50, 0, de="2026-10-07", ate="2026-10-07")
    assert recebidos["criado_desde"].isoformat() == "2026-10-07T00:00:00-03:00"
    assert recebidos["criado_ate"].isoformat().startswith("2026-10-07T23:59:59")
