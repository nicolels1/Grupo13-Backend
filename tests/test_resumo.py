from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from src.models.catalogo import ImagemProduto
from src.repositories import catalogo_repository, pedido_repository, permissao_repository, unidade_repository
from src.repositories import resumo_repository as repo
from src.use_cases import fidelidade, resumo, vendas
from src.use_cases.erros import RecursoNaoEncontrado, RegraDeNegocio, SemPermissao
from tests.apoio import SessaoFalsa, api, funcionario  # noqa: F401

HOJE = date(2026, 10, 7)
LOJA, CD, VULTO = 1, 2, 3


@pytest.fixture
def banco(monkeypatch):
    """Consultas do resumo trocadas por respostas prontas; `permissoes` decide o que a conta vê."""
    estado = SimpleNamespace(permissoes=set(), admin=False, unidade_filtrada=[], marca_filtrada=[], periodos=[],
                             com_app=False)
    m = monkeypatch.setattr
    m(resumo, "_hoje", lambda: HOJE)
    m(resumo, "usuario_tem_permissao", lambda db, u, *codigos: estado.admin or bool(set(codigos) & estado.permissoes))
    m(permissao_repository, "modelo_eh_admin", lambda db, i: estado.admin)
    m(unidade_repository, "buscar_unidade", lambda db, i: object() if i in (LOJA, CD) else None)

    def vendas_por_dia(db, desde, ate, id_unidade, marca):
        estado.unidade_filtrada.append(id_unidade)
        estado.marca_filtrada.append(marca)
        estado.periodos.append((desde, ate))
        linhas = [{"dia": HOJE, "canal_venda": "site", "pedidos": 2, "valor": Decimal("300.00")},
                  {"dia": HOJE - timedelta(days=13), "canal_venda": "loja", "pedidos": 1, "valor": Decimal("50.00")}]
        if estado.com_app:
            linhas.append({"dia": HOJE, "canal_venda": "app", "pedidos": 1, "valor": Decimal("80.00")})
        return linhas

    # o período anterior é o que não termina no fim de hoje
    def anterior(ate):
        return ate is not None and ate.date() != HOJE + timedelta(days=1)

    def vendas_por_canal(db, desde, ate=None, marca=None):
        estado.marca_filtrada.append(marca)
        return [{"canal_venda": "site", "pedidos": 2, "valor": Decimal("320.00")}] if anterior(ate) \
            else [{"canal_venda": "site", "pedidos": 4, "valor": Decimal("400.00")}]

    def vendas_por_marca(db, desde, ate=None, marca=None):
        linhas = [{"marca": "lorenzi", "pedidos": 2, "valor": Decimal("200.00")}] if anterior(ate) else [
            {"marca": "lorenzi", "pedidos": 3, "valor": Decimal("300.00")},
            {"marca": "vulto", "pedidos": 1, "valor": Decimal("150.00")}]
        return [l for l in linhas if marca in (None, l["marca"])]

    m(repo, "vendas_por_dia", vendas_por_dia)
    m(repo, "mais_vendidas", lambda db, desde, ate, u, marca, limite: [
        {"id_variante": 10, "id_produto": 5, "produto": "Camisa", "cor": "Azul", "tamanho": "M", "sku": "CAM-AZ-M",
         "quantidade_vendida": 7}])
    m(repo, "saldos_das_variantes", lambda db, ids, u: {10: 12})
    m(catalogo_repository, "imagens_dos_produtos", lambda db, ids: {5: [
        ImagemProduto(id_produto=5, cor=None, caminho_arquivo="produto-5/geral.png", ordem=1),
        ImagemProduto(id_produto=5, cor="Azul", caminho_arquivo="produto-5/azul.png", ordem=2)]})
    m(repo, "chamados_por_status", lambda db, desde, ate, u, marca: {"aberto": 3, "concluido": 4})
    m(repo, "chamados_por_dia", lambda db, desde, ate, u, marca: ({HOJE: 2}, {HOJE - timedelta(days=1): 1}))
    m(repo, "vendas_por_canal", vendas_por_canal)
    m(repo, "vendas_por_marca", vendas_por_marca)
    m(repo, "ruptura_online", lambda db, marca=None: (20, 3))
    m(repo, "primeira_resposta", lambda db, desde, ate=None, marca=None: (2.345, 1))
    m(repo, "avaliacoes", lambda db, desde, ate=None, marca=None: (4.25, 8, 2))
    m(repo, "saldo_total", lambda db, marca=None: 600)
    m(repo, "pecas_vendidas_por_unidade", lambda db, desde, ate=None, marca=None: {LOJA: 30, CD: 30})
    m(repo, "retiradas_perto_de_vencer", lambda db, antes: {LOJA: 2})
    m(repo, "unidades_ativas", lambda db, marca=None: [
        u for u in (SimpleNamespace(id_unidade=CD, nome="CD", tipo="cd", marca="lorenzi"),
                    SimpleNamespace(id_unidade=LOJA, nome="Loja", tipo="loja", marca="lorenzi"))
        if marca in (None, u.marca)])
    m(repo, "vendas_por_unidade", lambda db, desde, ate=None: {LOJA: (3, Decimal("90.00"))})
    m(fidelidade, "passivo", lambda db: [
        {"marca": "lorenzi", "pontos": 1000, "reais": Decimal("50.00"), "por_valor": []},
        {"marca": "vulto", "pontos": 4000, "reais": Decimal("120.00"), "por_valor": []}])
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
    assert set(dados) == {"id_unidade", "marca", "referencia", "gerado_em"}


def test_vendedor_ve_vendas_e_mais_vendidas(banco):
    banco.permissoes = {"registrar_venda_fisica"}
    dados = resumo.resumo(SessaoFalsa(), conta(), LOJA)
    assert set(dados) == {"id_unidade", "marca", "referencia", "gerado_em", "vendas_por_dia", "mais_vendidas"}
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
    assert dias[0]["loja"] == {"pedidos": 1, "valor": Decimal("50.00")}
    assert dias[-1]["site"] == {"pedidos": 2, "valor": Decimal("300.00")}
    assert dias[5]["site"] == {"pedidos": 0, "valor": Decimal("0.00")}


# o app foi descontinuado: só aparece quando o período tem venda dele (histórico da Vulto)
def test_app_so_aparece_quando_o_periodo_tem_venda_dele(banco):
    banco.permissoes = {"preparar_entregar_pedido"}
    assert "app" not in resumo.resumo(SessaoFalsa(), conta(), None)["vendas_por_dia"][0]
    banco.com_app = True
    dias = resumo.resumo(SessaoFalsa(), conta(), None)["vendas_por_dia"]
    assert dias[0]["app"] == {"pedidos": 0, "valor": Decimal("0.00")}
    assert dias[-1]["app"] == {"pedidos": 1, "valor": Decimal("80.00")}


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
    site = rede["vendas_14_dias"]["site"]
    assert (site["pedidos"], site["variacao_pedidos_pct"], site["variacao_valor_pct"]) == (4, 100.0, 25.0)
    assert rede["vendas_14_dias"]["loja"]["variacao_valor_pct"] is None  # sem base de comparação
    assert "app" not in rede["vendas_14_dias"]
    ticket = rede["ticket_medio_30_dias"]
    assert ticket == {"loja": None, "site": Decimal("100.00"), "total": Decimal("100.00")}
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
    assert loja["marca"] == "lorenzi"


# ---------- marca, referência e pontos ----------

def test_admin_ve_as_marcas_lado_a_lado(banco):
    banco.admin = True
    lorenzi, vulto = resumo.resumo(SessaoFalsa(), conta(), None)["rede_agora"]["por_marca"]

    assert (lorenzi["marca"], lorenzi["vendas_14_dias"]["pedidos"]) == ("lorenzi", 3)
    assert lorenzi["vendas_14_dias"]["variacao_valor_pct"] == 50.0  # 300 contra 200
    assert lorenzi["ticket_medio_30_dias"] == Decimal("100.00")
    assert vulto["vendas_14_dias"]["variacao_pedidos_pct"] is None  # sem base de comparação
    assert vulto["ticket_medio_30_dias"] == Decimal("150.00")


def test_filtro_de_marca_vale_para_todas_as_secoes(banco):
    banco.admin = True
    dados = resumo.resumo(SessaoFalsa(), conta(), None, marca="vulto")

    assert set(banco.marca_filtrada) == {"vulto"}
    assert [m["marca"] for m in dados["rede_agora"]["por_marca"]] == ["vulto"]
    assert dados["por_unidade"] == [] and dados["rede_agora"]["retiradas_perto_de_vencer"] == 0
    assert [p["marca"] for p in dados["pontos"]] == ["vulto"]


def test_referencia_termina_as_janelas_no_dia_escolhido(banco):
    banco.permissoes = {"registrar_venda_fisica"}
    referencia = date(2026, 9, 30)
    dados = resumo.resumo(SessaoFalsa(), conta(), None, referencia=referencia)

    desde, ate = banco.periodos[0]
    assert (desde.date(), ate.date()) == (date(2026, 9, 17), date(2026, 10, 1))
    assert dados["referencia"] == referencia and dados["vendas_por_dia"][-1]["dia"] == referencia


def test_referencia_no_futuro(banco):
    with pytest.raises(RegraDeNegocio):
        resumo.resumo(SessaoFalsa(), conta(), None, referencia=HOJE + timedelta(days=1))


def test_quem_gerencia_fidelidade_ve_os_pontos(banco):
    banco.permissoes = {"gerenciar_fidelidade"}
    pontos = resumo.resumo(SessaoFalsa(), conta(), None)["pontos"]
    assert [(p["marca"], p["pontos"], p["reais"]) for p in pontos] == [
        ("lorenzi", 1000, Decimal("50.00")), ("vulto", 4000, Decimal("120.00"))]


# ---------- rota ----------

def test_rota_deixa_ausente_a_secao_que_a_conta_nao_ve(api, banco, monkeypatch):
    banco.permissoes = {"atender_chamado"}
    # permitido=False: a conta não é Admin (o cliente de teste trata como Admin por padrão); vê as duas marcas
    cliente = api(SessaoFalsa(), usuario=conta(), permitido=False)
    monkeypatch.setattr(permissao_repository, "codigos_do_modelo", lambda db, i: {"ver_marca_lorenzi", "ver_marca_vulto"})
    corpo = cliente.get("/visao-geral/resumo").json()
    assert "chamados" in corpo and "vendas_por_dia" not in corpo and "rede_agora" not in corpo
    assert "pontos" not in corpo


def test_rota_mantem_null_dentro_das_secoes(api, banco):
    banco.admin = True
    banco.permissoes = set()
    corpo = api(SessaoFalsa(), usuario=conta()).get("/visao-geral/resumo").json()
    assert corpo["rede_agora"]["ticket_medio_30_dias"]["loja"] is None
    assert "app" not in corpo["rede_agora"]["ticket_medio_30_dias"]
    assert corpo["por_unidade"][0]["retiradas_perto_de_vencer"] is None


# ---------- filtro de data das vendas ----------

def test_vendas_de_hoje_filtram_pelo_dia_inteiro_de_brasilia(monkeypatch):
    recebidos = {}
    monkeypatch.setattr(pedido_repository, "listar_pedidos",
                        lambda db, limit, offset, **f: (recebidos.update(f), ([], 0))[1])
    vendas.listar(SessaoFalsa(), 50, 0, de="2026-10-07", ate="2026-10-07")
    assert recebidos["criado_desde"].isoformat() == "2026-10-07T00:00:00-03:00"
    assert recebidos["criado_ate"].isoformat().startswith("2026-10-07T23:59:59")


# ---------- consultas ----------

# a primeira resposta é a primeira fala da equipe visível ao cliente; a fala sem autor do atendimento
# migrado é do cliente (P6) e não conta como resposta
def test_primeira_resposta_nao_conta_a_fala_sem_autor():
    consultas = []

    class SessaoQueGuarda:
        def scalar(self, consulta):
            consultas.append(str(consulta.compile(dialect=postgresql.dialect())))
            return None

    repo.primeira_resposta(SessaoQueGuarda(), datetime(2026, 10, 1, tzinfo=timezone.utc))

    for sql in consultas:
        assert "mensagem.id_autor IS NOT NULL AND mensagem.id_autor IS DISTINCT FROM chamado.id_cliente" in sql
        assert "mensagem.interna IS false" in sql




# faturamento com a regra dos três números da Vulto, rodado num SQLite em memória (só as colunas usadas)
def test_faturamento_sem_frete_e_menos_os_estornos():
    banco = create_engine("sqlite://")
    pago = "2026-10-02 12:00:00.000000"
    with banco.begin() as c:
        c.execute(text("CREATE TABLE unidade (id_unidade int, marca text)"))
        c.execute(text("CREATE TABLE pedido (id_pedido int, pago_em text, status text, canal_venda text, "
                       "id_unidade int, valor_total numeric, valor_frete numeric)"))
        c.execute(text("CREATE TABLE pagamento (id_pagamento int, id_pedido int, tipo text, status text, "
                       "valor numeric, criado_em text)"))
        c.execute(text("INSERT INTO unidade VALUES (:id, :marca)"),
                  [dict(id=CD, marca="lorenzi"), dict(id=LOJA, marca="lorenzi"), dict(id=VULTO, marca="vulto")])
        c.execute(text("INSERT INTO pedido VALUES (:id, :pago, :status, :canal, :unidade, :total, :frete)"), [
            dict(id=1, pago=pago, status="entregue", canal="site", unidade=CD, total=100, frete=20),  # sem o frete
            dict(id=2, pago=pago, status="entregue", canal="site", unidade=CD, total=50, frete=10),  # estornado (V4)
            dict(id=3, pago=pago, status="entregue", canal="loja", unidade=LOJA, total=70, frete=0),  # V6
            dict(id=4, pago=pago, status="cancelado", canal="loja", unidade=LOJA, total=40, frete=0),
            dict(id=5, pago=None, status="aguardando_pagamento", canal="site", unidade=CD, total=90, frete=0),
            dict(id=6, pago=pago, status="entregue", canal="app", unidade=VULTO, total=60, frete=0),  # P3
        ])
        c.execute(text("INSERT INTO pagamento VALUES (:id, :pedido, 'estorno', :status, :valor, :quando)"), [
            dict(id=1, pedido=2, status="aprovado", valor=50, quando="2026-10-03 12:00:00.000000"),
            dict(id=2, pedido=3, status="aprovado", valor=30, quando="2026-10-05 12:00:00.000000"),
            dict(id=3, pedido=3, status="recusado", valor=70, quando="2026-10-05 12:00:00.000000"),
            dict(id=4, pedido=4, status="aprovado", valor=40, quando="2026-10-03 12:00:00.000000"),
        ])

    with Session(banco) as db:
        desde = datetime(2026, 10, 1)
        canais = {l["canal_venda"]: (l["pedidos"], l["valor"]) for l in repo.vendas_por_canal(db, desde)}
        unidades = repo.vendas_por_unidade(db, desde)
        so_a_venda = repo.vendas_por_canal(db, desde, datetime(2026, 10, 4))
        marcas = {l["marca"]: (l["pedidos"], l["valor"]) for l in repo.vendas_por_marca(db, desde)}
        so_lorenzi = {l["canal_venda"] for l in repo.vendas_por_canal(db, desde, marca="lorenzi")}

    assert canais == {"site": (2, 80), "loja": (1, 40), "app": (1, 60)}
    assert unidades == {CD: (2, 80), LOJA: (1, 40), VULTO: (1, 60)}
    # a devolução sai no dia em que aconteceu, fora do período que só tem a venda
    assert {l["canal_venda"]: l["valor"] for l in so_a_venda}["loja"] == 70
    # a marca da venda é a da unidade
    assert marcas == {"lorenzi": (3, 120), "vulto": (1, 60)}
    assert so_lorenzi == {"site", "loja"}


# ---------- faturamento ----------

def test_faturamento_soma_as_linhas_e_o_frete_a_parte(banco, monkeypatch):
    banco.admin = True
    periodos = []
    monkeypatch.setattr(repo, "faturamento_por_mes", lambda db, desde, ate, u, marca: periodos.append((desde, ate)) or [
        {"mes": date(2026, 9, 1), "id_unidade": LOJA, "unidade": "Loja", "canal_venda": "loja", "pedidos": 2,
         "valor": Decimal("150.00"), "frete": Decimal("0.00")},
        {"mes": date(2026, 9, 1), "id_unidade": CD, "unidade": "CD", "canal_venda": "app", "pedidos": 1,
         "valor": Decimal("90.00"), "frete": Decimal("12.50")}])

    dados = resumo.faturamento(SessaoFalsa(), conta(), date(2025, 1, 1), date(2026, 9, 30))

    assert (dados["valor"], dados["frete"], len(dados["items"])) == (Decimal("240.00"), Decimal("12.50"), 2)
    desde, ate = periodos[0]
    assert desde.isoformat() == "2025-01-01T00:00:00-03:00" and ate.isoformat() == "2026-10-01T00:00:00-03:00"


def test_faturamento_so_para_o_admin(banco):
    banco.permissoes = {"registrar_venda_fisica"}
    with pytest.raises(SemPermissao):
        resumo.faturamento(SessaoFalsa(), conta(), date(2026, 9, 1), date(2026, 9, 30))


def test_faturamento_com_o_fim_antes_do_inicio(banco):
    banco.admin = True
    with pytest.raises(RegraDeNegocio):
        resumo.faturamento(SessaoFalsa(), conta(), date(2026, 9, 30), date(2026, 9, 1))
