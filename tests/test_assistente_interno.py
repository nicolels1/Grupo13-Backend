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


def test_periodo_antes_dos_pedidos_diz_que_nao_ha_dado_em_vez_de_zero(vendas):
    r = ferramentas_internas.vendas_do_periodo(
        None, USUARIO, {"periodo": "intervalo", "de": "2024-01-01", "ate": "2024-12-31"}, QUARTA)

    assert r["sem_dados_antes_de"] == "01/01/2025"
    assert r["texto"] == r["aviso"]
    assert r["texto"].startswith("A plataforma não tem vendas antes de 01/01/2025 (ano 2025)")
    assert "R$" not in r["texto"]


def test_periodo_que_comeca_antes_dos_pedidos_avisa_que_cobre_so_uma_parte(vendas):
    r = ferramentas_internas.vendas_do_periodo(
        None, USUARIO, {"periodo": "intervalo", "de": "2024-06-01", "ate": "2025-05-31"}, QUARTA)

    assert r["texto"].startswith("Vendas na rede inteira de 01/06/2024 a 31/05/2025: R$ 50.030,50")
    assert r["texto"].endswith("o total acima cobre só a parte do período a partir dessa data.")


def test_periodo_depois_do_comeco_dos_pedidos_nao_tem_aviso(vendas):
    r = ferramentas_internas.vendas_do_periodo(None, USUARIO, {"periodo": "ultimos_30_dias"}, QUARTA)
    assert "aviso" not in r


# ---------- maiores_clientes ----------

@pytest.fixture
def clientes(vendas, monkeypatch):
    estado = SimpleNamespace(consultas=[], linhas=[
        {"id_cliente": uuid.UUID("cbee8556-dd06-4b9c-adc8-6094f0e8eacd"), "id_cliente_sem_conta": None,
         "pedidos": 5, "valor": Decimal("1739.40")},
        {"id_cliente": None, "id_cliente_sem_conta": 2754, "pedidos": 2, "valor": Decimal("849.70")},
    ], sem_cliente={"pedidos": 2, "valor": Decimal("599.80")})

    def maiores(db, desde, ate, id_unidade, marca, limite):
        estado.consultas.append((desde, ate, id_unidade, marca, limite))
        return estado.linhas[:limite], estado.sem_cliente

    monkeypatch.setattr(resumo_repository, "maiores_clientes", maiores)
    monkeypatch.setattr(resumo_repository, "nomes_dos_clientes", lambda db, conta, sem_conta: (
        {uuid.UUID("cbee8556-dd06-4b9c-adc8-6094f0e8eacd"): "Maria Souza"}, {}))
    return estado


def test_ranking_de_clientes_vem_sem_nome_e_o_nome_so_na_tabela_da_tela(clientes):
    r = ferramentas_internas.maiores_clientes(None, USUARIO, {"periodo": "este_mes"}, QUARTA)

    assert r["maiores_clientes"][0] == {"posicao": "1º", "faturamento": "R$ 1.739,40", "pedidos": "5",
                                        "ticket_medio": "R$ 347,88", "cadastro": "com conta"}
    assert r["maiores_clientes"][1]["cadastro"] == "sem conta (comprador migrado da Vulto)"
    assert r["tabela"]["colunas"] == ["Posição", "Cliente", "Total", "Pedidos"]
    assert r["tabela"]["linhas"] == [
        {"celulas": ["1º", "Maria Souza", "R$ 1.739,40", "5"],
         "url": "/interno/clientes/conta/cbee8556-dd06-4b9c-adc8-6094f0e8eacd"},
        {"celulas": ["2º", "Cliente sem nome (só CPF)", "R$ 849,70", "2"], "url": "/interno/clientes/sem-conta/2754"},
    ]
    assert r["sem_cliente"] == {"faturamento": "R$ 599,80", "pedidos": "2"}
    # o nome e o id do cliente só vão na tabela, que não chega ao modelo
    sem_tabela = str({k: v for k, v in r.items() if k != "tabela"})
    assert "Maria" not in sem_tabela and "cbee8556" not in sem_tabela and "2754" not in sem_tabela


def test_ranking_sem_periodo_usa_os_ultimos_365_dias(clientes):
    r = ferramentas_internas.maiores_clientes(None, USUARIO, {}, QUARTA)

    assert r["periodo"] == {"de": "29/10/2025", "ate": "28/10/2026", "padrao": "últimos 365 dias"}
    # a consulta traz os 10 da tabela; o modelo recebe só os 5 do padrão
    assert clientes.consultas[0][4] == 10


def test_pergunta_por_um_cliente_so_manda_um_ao_modelo_e_a_tabela_mostra_todos(clientes):
    r = ferramentas_internas.maiores_clientes(None, USUARIO, {"periodo": "este_mes", "quantos": 1}, QUARTA)

    assert [c["posicao"] for c in r["maiores_clientes"]] == ["1º"]
    assert [l["celulas"][0] for l in r["tabela"]["linhas"]] == ["1º", "2º"]


def test_ranking_de_clientes_vazio_diz_o_que_ficou_de_fora(clientes):
    clientes.linhas = []
    r = ferramentas_internas.maiores_clientes(None, USUARIO, {"periodo": "hoje"}, QUARTA)
    assert r["vazio"] is True and "balcão sem CPF" in r["texto"]


@pytest.mark.parametrize("quantos", [0, 11])
def test_ranking_de_clientes_fora_do_limite(clientes, quantos):
    with pytest.raises(RegraDeNegocio):
        ferramentas_internas.maiores_clientes(None, USUARIO, {"periodo": "hoje", "quantos": quantos}, QUARTA)


def test_ranking_de_clientes_pede_permissao_de_vendas(clientes, vendas):
    vendas.pode_vender = False
    with pytest.raises(SemPermissao):
        ferramentas_internas.maiores_clientes(None, USUARIO, {}, QUARTA)


def test_consulta_do_ranking_soma_por_cliente_e_separa_o_balcao():
    from sqlalchemy.dialects import postgresql

    class Capturar:
        def __init__(self):
            self.sqls = []

        def execute(self, consulta):
            self.sqls.append(str(consulta.compile(dialect=postgresql.dialect())))
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: [], one=lambda: {}))

    db = Capturar()
    resumo_repository.maiores_clientes(db, datetime.now(timezone.utc), datetime.now(timezone.utc), None, "vulto", 5)
    ranking, balcao = db.sqls
    assert "GROUP BY lancamento.id_cliente, lancamento.id_cliente_sem_conta" in ranking
    assert "HAVING sum(lancamento.valor) > " in ranking and "LIMIT" in ranking
    assert "NOT (lancamento.id_cliente IS NOT NULL OR lancamento.id_cliente_sem_conta IS NOT NULL)" in balcao


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


# ---------- estoque_em_um_dia ----------

class ConsultaFalsa:
    def __init__(self, linhas):
        self.linhas = linhas

    def limit(self, n):
        return self


class BancoDeEstoque:
    def __init__(self, linhas):
        self.linhas = linhas

    def execute(self, consulta):
        return SimpleNamespace(mappings=lambda: iter(consulta.linhas))


def linha_estoque(id_variante, produto, cor, tamanho, unidade, canal, quantidade):
    return {"id_variante": id_variante, "sku": f"SKU-{id_variante}", "produto": produto, "cor": cor,
            "tamanho": tamanho, "unidade": unidade, "canal": canal, "quantidade": quantidade}


@pytest.fixture
def estoque(monkeypatch):
    estado = SimpleNamespace(pode=True, marcas={"lorenzi", "vulto"}, consultas=[], linhas=[
        linha_estoque(1, "Camisa Cuban Ampla", "Bege", "M", "Vulto Savassi", "loja_fisica", 7),
        linha_estoque(1, "Camisa Cuban Ampla", "Bege", "M", "CD Cajamar", "online", 4),
        linha_estoque(1, "Camisa Cuban Ampla", "Bege", "M", "Vulto Savassi", "online", 1),
        linha_estoque(2, "Camisa Cuban Ampla", "Bege", "G", "Vulto Savassi", "loja_fisica", 3),
        linha_estoque(3, "Camisa Cuban Ampla", "Azul Marinho", "M", "Vulto Batel", "loja_fisica", 2),
    ])

    def consulta(em, id_unidade=None, busca=None, marca=None):
        estado.consultas.append({"em": em, "id_unidade": id_unidade, "busca": busca, "marca": marca})
        return ConsultaFalsa(estado.linhas)

    def paradas(corte, id_unidade=None, marca=None, cortes_por_categoria=None):
        estado.consultas.append({"corte": corte, "id_unidade": id_unidade, "marca": marca,
                                 "cortes_por_categoria": cortes_por_categoria})
        return ConsultaFalsa(estado.linhas)

    # o normal de cada categoria no último ano: (nome, dias, intervalos medidos)
    estado.medidos = {1: ("Jaquetas", 115.4, 221), 2: ("Acessórios", 9.0, 904), 3: ("Rara", 50.0, 5)}
    m = monkeypatch.setattr
    m(ferramentas_internas.estoque_repository, "consulta_historico", consulta)
    m(ferramentas_internas.estoque_repository, "consulta_pecas_paradas", paradas)
    estado.por_unidade = []
    m(ferramentas_internas.estoque_repository, "consulta_paradas_por_unidade",
      lambda consulta: ConsultaFalsa(estado.por_unidade))
    m(ferramentas_internas.estoque_repository, "prazos_por_categoria", lambda db, desde: estado.medidos)
    m(ferramentas_internas, "usuario_tem_permissao", lambda db, u, *codigos: estado.pode)
    m(ferramentas_internas, "marcas_visiveis", lambda db, u: estado.marcas)
    m(unidade_repository, "buscar_unidade", lambda db, i: SAVASSI if i == 5 else None)
    return estado


def em_estoque(estado, parametros):
    return ferramentas_internas.estoque_em_um_dia(BancoDeEstoque(estado.linhas), USUARIO, parametros, QUARTA)


def test_estoque_soma_os_canais_por_unidade_e_filtra_cor_e_tamanho(estoque):
    r = em_estoque(estoque, {"peca": "cuban", "cor": "bege", "tamanho": "m"})

    assert len(r["pecas"]) == 1
    peca = r["pecas"][0]
    assert (peca["produto"], peca["cor"], peca["tamanho"], peca["total"]) == ("Camisa Cuban Ampla", "Bege", "M", "12")
    assert peca["por_unidade"] == [
        {"unidade": "CD Cajamar", "loja_fisica": "0", "online": "4", "total": "4"},
        {"unidade": "Vulto Savassi", "loja_fisica": "7", "online": "1", "total": "8"},
    ]
    assert r["momento"] == "agora"
    assert r["texto"] == "Estoque na rede inteira agora: Camisa Cuban Ampla Bege M: 12 peças."
    assert estoque.consultas[0]["busca"] == "cuban"


def test_estoque_numa_data_passada_vale_o_fim_do_dia_em_brasilia(estoque):
    r = em_estoque(estoque, {"peca": "cuban", "data": "2026-09-30", "id_unidade": 5})

    em = estoque.consultas[0]["em"].astimezone(assistente_interno.BRASILIA)
    assert (em.date(), em.hour, em.minute) == (date(2026, 9, 30), 23, 59)
    assert estoque.consultas[0]["id_unidade"] == 5
    assert r["momento"] == "no fim de 30/09/2026" and r["onde"] == "na unidade Vulto Savassi"
    assert r["links"][0]["url"] == "/interno/estoque/historico?data=2026-09-30&hora=23:59&unidade=5"


def test_muitas_pecas_pede_para_escolher(estoque, monkeypatch):
    monkeypatch.setattr(ferramentas_internas, "PECAS_POR_RESPOSTA", 2)

    r = em_estoque(estoque, {"peca": "camisa"})

    assert r["precisa_escolher"] is True and r["variacoes_encontradas"] == "3"
    assert r["total_de_pecas"] == "17"
    assert "pecas" not in r
    assert r["exemplos"] == ["Camisa Cuban Ampla (Azul Marinho)", "Camisa Cuban Ampla (Bege)"]
    assert r["texto"].startswith('Achei 3 variações (cor e tamanho) com "camisa", somando 17 peças')


@pytest.mark.parametrize("pedido, cor, bate", [
    ("preta", "Preto", True), ("branca", "Branco", True), ("lilas", "Lilás", True), ("azul", "Azul Marinho", True),
    ("rosa", "Rosa", True), ("preta", "Bege", False),
])
def test_cor_como_a_pessoa_fala(pedido, cor, bate):
    assert ferramentas_internas._bate(cor, pedido) is bate


def test_sem_peca_e_o_total_da_unidade_por_canal(estoque):
    r = em_estoque(estoque, {"data": "2026-10-01", "id_unidade": 5})

    assert estoque.consultas[0]["busca"] is None
    assert (r["total_de_pecas"], r["loja_fisica"], r["online"], r["variacoes_com_saldo"]) == ("17", "12", "5", "3")
    assert r["por_unidade"][0] == {"unidade": "Vulto Savassi", "loja_fisica": "10", "online": "1", "total": "11"}
    assert r["texto"] == ("Estoque na unidade Vulto Savassi no fim de 01/10/2026: 17 peças (12 na loja física e 5 no "
                          "online), em 3 variações com saldo.")


def test_total_sem_peca_aceita_cor(estoque):
    r = em_estoque(estoque, {"cor": "azul"})
    assert (r["total_de_pecas"], r["filtro"]) == ("2", "azul")
    assert r["texto"].startswith("Estoque (azul) na rede inteira agora: 2 peças")


def test_peca_que_nao_existe(estoque):
    estoque.linhas = []
    r = em_estoque(estoque, {"peca": "xyz"})
    assert r["vazio"] is True and "Não achei" in r["texto"]


@pytest.mark.parametrize("parametros", [
    {"peca": "x"}, {"peca": "cuban", "data": "2026-10-29"}, {"peca": "cuban", "data": "2024-12-31"},
    {"peca": "cuban", "data": "30/09/2026"},
])
def test_estoque_com_parametro_invalido(estoque, parametros):
    with pytest.raises(RegraDeNegocio):
        em_estoque(estoque, parametros)


def test_estoque_pede_permissao_de_estoque_e_a_marca(estoque):
    estoque.pode = False
    with pytest.raises(SemPermissao):
        em_estoque(estoque, {"peca": "cuban"})
    estoque.pode, estoque.marcas = True, {"lorenzi"}
    with pytest.raises(SemPermissao):
        em_estoque(estoque, {"peca": "cuban", "id_unidade": 5})


# ---------- pecas_paradas ----------

def test_consulta_de_paradas_usa_a_ultima_venda_ou_a_chegada():
    from sqlalchemy.dialects import postgresql
    from src.repositories import estoque_repository

    texto = str(estoque_repository.consulta_pecas_paradas(datetime.now(timezone.utc), id_unidade=5, marca="vulto")
                .compile(dialect=postgresql.dialect()))

    assert "max(movimentacao_estoque.criado_em) FILTER (WHERE movimentacao_estoque.tipo = %(tipo_1)s" in texto
    assert "min(movimentacao_estoque.criado_em) AS chegou_em" in texto
    assert "WHERE coalesce(anon_2.ultima_venda, anon_2.chegou_em) <" in texto
    assert "HAVING sum(estoque.quantidade) > %(sum_1)s" in texto
    assert "estoque.id_unidade = %(id_unidade_1)s" in texto and "unidade.marca = %(marca_1)s" in texto


def test_consulta_de_paradas_com_um_corte_por_categoria():
    from sqlalchemy.dialects import postgresql
    from src.repositories import estoque_repository

    agora = datetime.now(timezone.utc)
    texto = str(estoque_repository.consulta_pecas_paradas(agora, cortes_por_categoria={1: agora, 2: agora})
                .compile(dialect=postgresql.dialect()))
    assert "coalesce(anon_2.ultima_venda, anon_2.chegou_em) < CASE produto.id_categoria WHEN" in texto


def test_normal_da_categoria_e_o_percentil_90_das_vendas_das_lojas():
    from sqlalchemy.dialects import postgresql
    from src.repositories import estoque_repository

    class Capturar:
        def execute(self, consulta):
            self.sql = str(consulta.compile(dialect=postgresql.dialect()))
            return []

    db = Capturar()
    estoque_repository.prazos_por_categoria(db, datetime.now(timezone.utc))
    assert "lag(movimentacao_estoque.criado_em) OVER (PARTITION BY movimentacao_estoque.id_variante, " in db.sql
    assert "percentile_cont(%(percentile_cont_1)s) WITHIN GROUP" in db.sql
    assert "movimentacao_estoque.tipo = " in db.sql and "unidade.tipo = " in db.sql


def parada(produto, unidade, quantidade, ultima_venda, desde, total_linhas=40, total_pecas=300, id_categoria=1):
    return {"id_variante": 1, "sku": "SKU", "produto": produto, "cor": "Preto", "tamanho": "M", "id_unidade": 5,
            "unidade": unidade, "quantidade": quantidade, "ultima_venda": ultima_venda, "parada_desde": desde,
            "total_linhas": total_linhas, "total_pecas": total_pecas, "id_categoria": id_categoria}


def no_dia(momento) -> datetime:
    return momento.astimezone(assistente_interno.BRASILIA).replace(tzinfo=None)


def test_sem_prazo_cada_categoria_usa_o_normal_dela(estoque):
    venda = datetime(2026, 8, 1, 15, 0, tzinfo=timezone.utc)
    chegada = datetime(2025, 1, 1, 3, 0, tzinfo=timezone.utc)
    estoque.linhas = [parada("Bomber Clássica", "Vulto Savassi", 20, None, chegada),
                      parada("Boné Logo", "Vulto Batel", 3, venda, venda, id_categoria=2)]
    r = ferramentas_internas.pecas_paradas(BancoDeEstoque(estoque.linhas), USUARIO, {}, QUARTA)

    cortes = estoque.consultas[0]["cortes_por_categoria"]
    assert no_dia(cortes[1]) == datetime(2026, 7, 5)  # Jaquetas: 115 dias antes de 28/10
    assert no_dia(cortes[2]) == datetime(2026, 10, 13)  # Acessórios: 9 medidos, mas o mínimo é 15
    assert no_dia(cortes[3]) == datetime(2026, 8, 29)  # pouca venda para medir: o padrão de 60
    assert r["regra"] == "há mais tempo que o normal da categoria" and "dias" not in r
    assert r["prazos_por_categoria"][0] == {"categoria": "Acessórios", "dias": "15"}
    assert r["mais_antigas"][0]["normal_da_categoria"] == "115 dias (Jaquetas)"
    assert r["mais_antigas"][0]["ultima_venda"] == "nunca vendeu nessa unidade"
    # estava lá quando o histórico começou e nunca vendeu: o tempo real pode ser maior
    assert r["mais_antigas"][0]["dias_parada"] == "pelo menos 665"
    assert r["mais_antigas"][1]["ultima_venda"] == "01/08/2026" and r["mais_antigas"][1]["dias_parada"] == "88"
    assert r["texto"].startswith("40 variações paradas (há mais tempo que o normal da categoria) na rede inteira, "
                                 "somando 300 peças.")


def test_prazo_pedido_vale_para_todas_as_categorias(estoque):
    estoque.linhas = [parada("Bomber Clássica", "Vulto Savassi", 20, None, datetime(2025, 1, 1, 3, 0, tzinfo=timezone.utc))]
    r = ferramentas_internas.pecas_paradas(BancoDeEstoque(estoque.linhas), USUARIO, {"dias": 60}, QUARTA)

    consulta = estoque.consultas[0]
    assert consulta["cortes_por_categoria"] is None and no_dia(consulta["corte"]) == datetime(2026, 8, 29)
    assert (r["regra"], r["dias"], r["prazos_por_categoria"]) == ("há mais de 60 dias", "60", [])
    assert "normal_da_categoria" not in r["mais_antigas"][0]


def test_paradas_separam_o_total_de_cada_loja_e_de_cada_cd(estoque):
    chegou_depois = datetime(2025, 3, 10, 15, 0, tzinfo=timezone.utc)
    estoque.linhas = [parada("Bomber Clássica", "CD Cajamar", 20, None, chegou_depois)]
    estoque.por_unidade = [
        {"unidade": "Vulto Savassi", "tipo_unidade": "loja", "variacoes": 120, "pecas": 410},
        {"unidade": "CD Cajamar", "tipo_unidade": "cd", "variacoes": 900, "pecas": 3200},
    ]
    r = ferramentas_internas.pecas_paradas(BancoDeEstoque(estoque.linhas), USUARIO, {}, QUARTA)

    assert r["lojas"] == [{"unidade": "Vulto Savassi", "variacoes": "120", "pecas": "410"}]
    assert r["cds"] == [{"unidade": "CD Cajamar", "variacoes": "900", "pecas": "3.200"}]
    # chegou depois do começo do histórico: a contagem é exata
    assert r["mais_antigas"][0]["dias_parada"] == "597"


def test_consulta_de_paradas_por_unidade_soma_a_mesma_consulta():
    from sqlalchemy.dialects import postgresql
    from src.repositories import estoque_repository

    paradas = estoque_repository.consulta_pecas_paradas(datetime.now(timezone.utc), marca="vulto")
    texto = str(estoque_repository.consulta_paradas_por_unidade(paradas).compile(dialect=postgresql.dialect()))
    assert "count(*) AS variacoes" in texto and "sum(anon_1.quantidade) AS pecas" in texto
    assert "GROUP BY anon_1.id_unidade, anon_1.unidade, anon_1.tipo_unidade" in texto
    assert "ORDER BY anon_1.tipo_unidade DESC" in texto


def test_nada_parado(estoque):
    estoque.linhas = []
    r = ferramentas_internas.pecas_paradas(BancoDeEstoque([]), USUARIO, {"dias": 30}, QUARTA)
    assert r["vazio"] is True and r["texto"] == "Nenhuma peça parada (há mais de 30 dias) na rede inteira."


@pytest.mark.parametrize("dias", [0, 10, 400])
def test_dias_fora_do_limite(estoque, dias):
    with pytest.raises(RegraDeNegocio):
        ferramentas_internas.pecas_paradas(BancoDeEstoque([]), USUARIO, {"dias": dias}, QUARTA)


def test_paradas_pedem_permissao_de_estoque(estoque):
    estoque.pode = False
    with pytest.raises(SemPermissao):
        ferramentas_internas.pecas_paradas(BancoDeEstoque([]), USUARIO, {}, QUARTA)


# ---------- pontos_vencendo ----------

@pytest.fixture
def pontos(monkeypatch):
    estado = SimpleNamespace(pode=True, marcas={"lorenzi", "vulto"}, vencendo=[], proximos=[], chamadas=[])

    def vencendo(db, desde, ate, marca=None):
        estado.chamadas.append({"desde": desde, "ate": ate, "marca": marca})
        return estado.vencendo

    m = monkeypatch.setattr
    m(ferramentas_internas.fidelidade_repository, "pontos_vencendo", vencendo)
    m(ferramentas_internas.fidelidade_repository, "proximo_vencimento", lambda db, desde, marca=None: estado.proximos)
    m(ferramentas_internas, "usuario_tem_permissao", lambda db, u, *codigos: estado.pode and codigos == (
        "gerenciar_fidelidade",))
    m(ferramentas_internas, "marcas_visiveis", lambda db, u: estado.marcas)
    return estado


def test_pontos_vencendo_por_marca_sem_somar_as_marcas(pontos):
    vence = datetime(2026, 11, 10, 3, 0, tzinfo=timezone.utc)
    ganho = {"ganho_desde": datetime(2025, 1, 1, 15, 0, tzinfo=timezone.utc),
             "ganho_ate": datetime(2026, 9, 30, 15, 0, tzinfo=timezone.utc)}
    mesmo_dia = {"ganho_desde": datetime(2026, 10, 22, 15, 0, tzinfo=timezone.utc),
                 "ganho_ate": datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)}
    pontos.vencendo = [
        {"marca": "lorenzi", "clientes": 2, "pontos": 1398, "reais": Decimal("69.9"), "primeiro": vence, **mesmo_dia},
        {"marca": "vulto", "clientes": 2548, "pontos": 7128540, "reais": Decimal("514385.9"), "primeiro": vence,
         **ganho},
    ]

    r = ferramentas_internas.pontos_vencendo(None, USUARIO, {}, QUARTA)

    assert [m["marca"] for m in r["por_marca"]] == ["Casa Lorenzi", "Vulto"]
    assert r["por_marca"][1] == {"marca": "Vulto", "clientes": "2.548", "pontos": "7.128.540",
                                 "valor": "R$ 514.385,90", "primeiro_vencimento": "10/11/2026",
                                 "ganhos_entre": "de 01/01/2025 a 30/09/2026"}
    assert r["por_marca"][0]["ganhos_entre"] == "em 22/10/2026"
    assert r["prazo"] == "até 27/11/2026 (30 dias)"
    # até o fim do último dia do prazo, no horário de Brasília
    ate = pontos.chamadas[0]["ate"].astimezone(assistente_interno.BRASILIA)
    assert (ate.date(), ate.hour) == (date(2026, 11, 28), 0)
    assert "8.526.938" not in r["texto"]  # a soma das duas marcas não aparece


def test_nada_vencendo_diz_o_proximo_vencimento(pontos):
    pontos.proximos = [{"marca": "vulto", "dia": date(2027, 10, 1), "pontos": 7126923,
                        "ganho_desde": datetime(2025, 1, 1, 15, 0, tzinfo=timezone.utc),
                        "ganho_ate": datetime(2026, 9, 30, 15, 0, tzinfo=timezone.utc)}]

    r = ferramentas_internas.pontos_vencendo(None, USUARIO, {"marca": "vulto"}, QUARTA)

    assert r["vazio"] is True
    assert r["texto"] == ("Nenhum ponto vence até 27/11/2026 na Vulto. O próximo vencimento: na Vulto, 01/10/2027 "
                          "(7.126.923 pontos).")
    assert pontos.chamadas[0]["marca"] == "vulto"
    assert r["proximo_vencimento"][0]["ganhos_entre"] == "de 01/01/2025 a 30/09/2026"


def test_pontos_pedem_permissao_de_fidelidade_e_a_marca(pontos):
    pontos.pode = False
    with pytest.raises(SemPermissao):
        ferramentas_internas.pontos_vencendo(None, USUARIO, {}, QUARTA)
    pontos.pode, pontos.marcas = True, {"vulto"}
    with pytest.raises(SemPermissao):
        ferramentas_internas.pontos_vencendo(None, USUARIO, {"marca": "lorenzi"}, QUARTA)


@pytest.mark.parametrize("dias", [0, 367])
def test_prazo_de_pontos_fora_do_limite(pontos, dias):
    with pytest.raises(RegraDeNegocio):
        ferramentas_internas.pontos_vencendo(None, USUARIO, {"dias": dias}, QUARTA)


def test_consultas_de_pontos_montam_para_o_postgres():
    from sqlalchemy.dialects import postgresql
    from src.repositories import fidelidade_repository

    class Capturar:
        def execute(self, consulta):
            self.sql = str(consulta.compile(dialect=postgresql.dialect()))
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: []))

    agora = datetime.now(timezone.utc)
    db = Capturar()
    fidelidade_repository.pontos_vencendo(db, agora, agora, "vulto")
    assert "count(distinct(coalesce(" in db.sql and "lote_pontos.restante > " in db.sql
    fidelidade_repository.proximo_vencimento(db, agora, "vulto")
    assert "date(timezone(%(timezone_1)s" in db.sql and "lote_pontos.vence_em))" in db.sql


# ---------- desempenho_equipe ----------

@pytest.fixture
def equipe(monkeypatch):
    estado = SimpleNamespace(admin=True, marcas={"lorenzi", "vulto"}, consultas=[], vendas=[
        {"vendedor": "Fernanda Kowalski", "pedidos": 53, "valor": Decimal("21817.33"), "unidades": 1},
        {"vendedor": "Patrícia Wolff", "pedidos": 53, "valor": Decimal("20335.73"), "unidades": 1},
    ], chamados=[{"atendente": "Júlia", "concluidos": 13, "mediana_horas": None},
                 {"atendente": "Renata", "concluidos": 7, "mediana_horas": 5.25}])

    def vendas(db, desde, ate, id_unidade, marca):
        estado.consultas.append(("vendas", desde, ate, id_unidade, marca))
        return estado.vendas

    def chamados(db, desde, ate, id_unidade, marca):
        estado.consultas.append(("chamados", desde, ate, id_unidade, marca))
        return estado.chamados

    m = monkeypatch.setattr
    m(resumo_repository, "vendas_por_vendedor", vendas)
    m(resumo_repository, "chamados_por_atendente", chamados)
    m(ferramentas_internas.permissao_repository, "modelo_eh_admin", lambda db, i: estado.admin)
    m(ferramentas_internas, "marcas_visiveis", lambda db, u: estado.marcas)
    return estado


def test_desempenho_traz_vendedores_e_atendentes_do_periodo(equipe):
    r = ferramentas_internas.desempenho_equipe(None, USUARIO, {"periodo": "mes_passado"}, QUARTA)

    assert r["vendas_por_vendedor"][0] == {"vendedor": "Fernanda Kowalski", "pedidos": "53",
                                           "faturamento": "R$ 21.817,33", "ticket_medio": "R$ 411,65"}
    assert r["chamados_por_atendente"] == [
        {"atendente": "Júlia", "concluidos": "13", "mediana_horas_ate_concluir": None},
        {"atendente": "Renata", "concluidos": "7", "mediana_horas_ate_concluir": "5,3"},
    ]
    assert (r["vendedores"], r["atendentes"]) == ("2", "2")
    assert r["texto"] == ("Desempenho da equipe na rede inteira de 01/09/2026 a 30/09/2026: quem mais vendeu na "
                          "loja física foi Fernanda Kowalski, com R$ 21.817,33 em 53 pedidos; quem mais concluiu "
                          "chamados foi Júlia, com 13.")
    _, desde, ate, _, _ = equipe.consultas[0]
    assert desde.astimezone(assistente_interno.BRASILIA).date() == date(2026, 9, 1)
    assert ate.astimezone(assistente_interno.BRASILIA).date() == date(2026, 10, 1)


def test_desempenho_de_uma_area_so(equipe):
    r = ferramentas_internas.desempenho_equipe(None, USUARIO, {"periodo": "este_mes", "area": "atendimento"}, QUARTA)

    assert "vendas_por_vendedor" not in r and [c[0] for c in equipe.consultas] == ["chamados"]


def test_desempenho_sem_nada_no_periodo(equipe):
    equipe.vendas, equipe.chamados = [], []
    r = ferramentas_internas.desempenho_equipe(None, USUARIO, {"periodo": "hoje"}, QUARTA)
    assert r["vazio"] is True and r["texto"].startswith("Nenhuma venda na loja física nem chamado concluído")


def test_desempenho_so_para_o_admin(equipe):
    equipe.admin = False
    with pytest.raises(SemPermissao):
        ferramentas_internas.desempenho_equipe(None, USUARIO, {"periodo": "hoje"}, QUARTA)


def test_consultas_de_desempenho_montam_para_o_postgres():
    from sqlalchemy.dialects import postgresql

    class Capturar:
        def execute(self, consulta):
            self.sql = str(consulta.compile(dialect=postgresql.dialect()))
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: []))

    agora = datetime.now(timezone.utc)
    db = Capturar()
    resumo_repository.vendas_por_vendedor(db, agora, agora, 5, "vulto")
    assert "pedido.canal_venda = " in db.sql and "pedido.id_registrado_por" in db.sql
    assert "pedido.status != " in db.sql
    resumo_repository.chamados_por_atendente(db, agora, agora, None, None)
    # os migrados (concluído = aberto) ficam fora da mediana
    assert "CASE WHEN (chamado.concluido_em > chamado.criado_em)" in db.sql
    assert "percentile_cont(" in db.sql


# ---------- metricas_assistente ----------

@pytest.fixture
def metricas(monkeypatch):
    estado = SimpleNamespace(admin=False, atende=True, marcas={"lorenzi", "vulto"}, chamadas=[], numeros={
        "conversas": 80, "resolvidas": 62, "viraram_chamado": 10, "negativas": 5, "erro_do_sistema": 3,
        "se_identificaram": 30, "avaliadas": 40, "positivas": 34, "so_fora_do_tema": 6, "sem_assunto": 9,
        "por_assunto": [{"assunto": "estoque", "conversas": 41}, {"assunto": "pecas", "conversas": 35}],
    })

    def resumo(db, desde, ate, fechadas_antes, canal=None):
        estado.chamadas.append({"desde": desde, "ate": ate, "fechadas_antes": fechadas_antes, "canal": canal})
        return estado.numeros

    m = monkeypatch.setattr
    m(ferramentas_internas.assistente_interno_repository, "resumo_das_conversas", resumo)
    m(ferramentas_internas.permissao_repository, "modelo_eh_admin", lambda db, i: estado.admin)
    m(ferramentas_internas, "usuario_tem_permissao", lambda db, u, *codigos: estado.atende and codigos == (
        "atender_chamado",))
    m(ferramentas_internas, "marcas_visiveis", lambda db, u: estado.marcas)
    return estado


def test_metricas_no_formato_combinado_com_a_nicole(metricas):
    r = ferramentas_internas.metricas_assistente(None, USUARIO, {"periodo": "este_mes"}, QUARTA)

    assert r["grupos"] == {"resolvidas": "62", "viraram_chamado": "10", "avaliacao_negativa": "5",
                           "erro_do_sistema": "3"}
    assert (r["conversas"], r["taxa_resolvidas"]) == ("80", "78%")
    assert (r["se_identificaram"], r["taxa_se_identificaram"]) == ("30", "38%")
    # a avaliação é opcional: a porcentagem positiva é só entre as avaliadas
    assert (r["avaliadas"], r["positivas_entre_avaliadas"]) == ("40", "85%")
    assert r["por_assunto"] == [{"assunto": "estoque", "conversas": "41"}, {"assunto": "peças", "conversas": "35"}]
    assert (r["so_fora_do_tema"], r["sem_assunto"]) == ("6", "9")
    assert r["texto"] == ("Assistente do cliente no WhatsApp e no Telegram de 01/10/2026 a 28/10/2026: 80 conversas, 62 "
                          "resolvidas, 10 viraram chamado, 5 com 👎 e 3 com erro do sistema.")


def test_metricas_de_um_canal_so(metricas):
    r = ferramentas_internas.metricas_assistente(None, USUARIO, {"periodo": "hoje", "canal": "telegram"}, QUARTA)
    assert metricas.chamadas[0]["canal"] == "telegram"
    assert r["onde"] == "no Telegram"


def test_metricas_deixam_de_fora_as_conversas_em_andamento(metricas):
    ferramentas_internas.metricas_assistente(None, USUARIO, {"periodo": "hoje", "canal": "telegram"}, QUARTA)

    chamada = metricas.chamadas[0]
    assert chamada["canal"] == "telegram"
    assert datetime.now(timezone.utc) - chamada["fechadas_antes"] >= timedelta(minutes=59)


def test_metricas_sem_periodo_usam_os_ultimos_30_dias(metricas):
    r = ferramentas_internas.metricas_assistente(None, USUARIO, {}, QUARTA)
    assert r["periodo"] == {"de": "29/09/2026", "ate": "28/10/2026"}


def test_sem_conversas(metricas):
    metricas.numeros = {**metricas.numeros, "conversas": 0, "por_assunto": []}
    r = ferramentas_internas.metricas_assistente(None, USUARIO, {"periodo": "hoje"}, QUARTA)
    assert r["vazio"] is True and r["texto"] == (
        "Nenhuma conversa do assistente do cliente no WhatsApp e no Telegram em 28/10/2026."
    )


def test_metricas_pedem_atendimento_ou_admin_e_as_duas_marcas(metricas):
    metricas.atende = False
    with pytest.raises(SemPermissao):
        ferramentas_internas.metricas_assistente(None, USUARIO, {"periodo": "hoje"}, QUARTA)
    metricas.admin = True
    assert ferramentas_internas.metricas_assistente(None, USUARIO, {"periodo": "hoje"}, QUARTA)["conversas"] == "80"
    # o resumo não guarda a marca: quem só vê uma não consulta
    metricas.marcas = {"vulto"}
    with pytest.raises(SemPermissao):
        ferramentas_internas.metricas_assistente(None, USUARIO, {"periodo": "hoje"}, QUARTA)


def test_consulta_das_metricas_monta_para_o_postgres():
    from sqlalchemy.dialects import postgresql

    # as contagens (uma linha) e os assuntos (várias linhas, vazias aqui)
    class Resultado(list):
        def mappings(self):
            return SimpleNamespace(one=lambda: {})

    class Capturar:
        def __init__(self):
            self.sqls = []

        def execute(self, consulta):
            self.sqls.append(str(consulta.compile(dialect=postgresql.dialect())))
            return Resultado()

    agora = datetime.now(timezone.utc)
    db = Capturar()
    assistente_interno_repository.resumo_das_conversas(db, agora, agora, agora, "telegram")
    contagens, assuntos = db.sqls
    assert "unnest(resumo_conversa.assuntos)" in assuntos
    assert "resumo_conversa.assuntos && ARRAY[" in contagens
    assert "count(*) FILTER (WHERE" in contagens and "cardinality(resumo_conversa.assuntos)" in contagens
    assert "resumo_conversa.avaliacao IS DISTINCT FROM" in contagens
    assert "resumo_conversa.canal = " in contagens


# ---------- anomalias ----------

@pytest.fixture
def anomalias(monkeypatch):
    agosto = datetime(2026, 8, 14, 14, 42, tzinfo=timezone.utc)
    estado = SimpleNamespace(admin=True, marcas={"lorenzi", "vulto"}, chamadas=[], resultados={
        "precos_fora_do_catalogo": [{"codigo_venda": "VLT-17274", "pago_em": agosto, "unidade": "Vulto Pinheiros",
                                     "produto": "Camiseta Boxy Logo", "sku": "VLT-CAM-004-BRA-P",
                                     "preco_unitario": Decimal("59.90"), "preco_catalogo": Decimal("149.90"),
                                     "total": 4}],
        "vendas_fora_do_padrao": [{"codigo_venda": "VLT-17274", "pago_em": agosto, "canal_venda": "loja",
                                   "unidade": "Vulto Pinheiros", "valor": Decimal("23960.00"), "mediana": 339.9,
                                   "total": 1}],
        "pedidos_repetidos": [],
    })
    m = monkeypatch.setattr
    for nome, linhas in estado.resultados.items():
        def consulta(db, desde, ate, id_unidade, marca, _nome=nome, _linhas=linhas):
            estado.chamadas.append((_nome, desde, ate, id_unidade, marca))
            return _linhas
        m(resumo_repository, nome, consulta)
    m(resumo_repository, "estoque_negativo", lambda db, id_unidade, marca: [])
    m(ferramentas_internas.permissao_repository, "modelo_eh_admin", lambda db, i: estado.admin)
    m(ferramentas_internas, "marcas_visiveis", lambda db, u: estado.marcas)
    return estado


def test_anomalias_trazem_contagem_e_exemplos_com_o_codigo_da_venda(anomalias):
    r = ferramentas_internas.anomalias(None, USUARIO, {"periodo": "mes_passado"}, QUARTA)

    preco = r["verificacoes"]["preco_fora_do_catalogo"]
    assert preco["itens"] == "4"
    assert preco["exemplos"][0] == {"venda": "VLT-17274", "dia": "14/08/2026", "unidade": "Vulto Pinheiros",
                                    "peca": "Camiseta Boxy Logo (VLT-CAM-004-BRA-P)", "vendido_por": "R$ 59,90",
                                    "catalogo_na_epoca": "R$ 149,90"}
    assert r["verificacoes"]["venda_fora_do_padrao"]["exemplos"][0]["mediana_do_canal"] == "R$ 339,90"
    assert r["verificacoes"]["pedido_repetido"] == {"pares": "0", "exemplos": []}
    assert r["vazio"] is False
    assert r["texto"] == ("Anomalias na rede inteira de 01/09/2026 a 30/09/2026: 4 itens vendidos longe do preço do "
                          "catálogo; 1 venda muito acima do normal do canal; 0 pares de pedidos repetidos; 0 saldos "
                          "de estoque negativos (agora).")


def test_anomalias_sem_periodo_usam_os_ultimos_30_dias(anomalias):
    r = ferramentas_internas.anomalias(None, USUARIO, {}, QUARTA)
    assert r["periodo"] == {"de": "29/09/2026", "ate": "28/10/2026"}


def test_anomalias_so_para_o_admin(anomalias):
    anomalias.admin = False
    with pytest.raises(SemPermissao):
        ferramentas_internas.anomalias(None, USUARIO, {}, QUARTA)


def test_consultas_de_anomalias_montam_para_o_postgres():
    from sqlalchemy.dialects import postgresql

    class Capturar:
        def execute(self, consulta):
            self.sql = str(consulta.compile(dialect=postgresql.dialect()))
            return SimpleNamespace(mappings=lambda: [])

    agora = datetime.now(timezone.utc)
    db = Capturar()
    resumo_repository.precos_fora_do_catalogo(db, agora, agora, 5, "vulto")
    # o preço da época: faixas do histórico (lead), e sem histórico o de hoje
    assert "lead(historico_preco.alterado_em) OVER (PARTITION BY historico_preco.id_variante" in db.sql
    assert "coalesce(anon_1.preco, variante.preco) > " in db.sql
    resumo_repository.vendas_fora_do_padrao(db, agora, agora, None, None)
    assert "percentile_cont(" in db.sql and "HAVING count(*) >= " in db.sql
    resumo_repository.pedidos_repetidos(db, agora, agora, None, None)
    assert "outro.id_pedido > pedido.id_pedido" in db.sql and "outro.status != " in db.sql
    resumo_repository.estoque_negativo(db, 5, "vulto")
    assert "estoque.quantidade < " in db.sql and "unidade.marca = " in db.sql


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


def test_ontem_hoje_e_amanha_passam_e_o_numero_das_instrucoes_tambem():
    texto = "Não faço previsão. Amanhã é 29/10/2026 e ontem foi 27/10/2026. A unidade 45 é a Savassi."
    instrucoes = "Hoje é quarta, 28/10/2026. Unidades: 45: Vulto Savassi"
    assert numeros_conferem(texto, [], "quanto vamos vender amanhã?", instrucoes, QUARTA)


def test_pontas_do_ano_e_do_mes_citados_passam():
    assert numeros_conferem("De 01/01/2025 a 31/12/2025.", [], "quanto vendemos em 2025?", hoje=QUARTA)
    assert numeros_conferem("De 01/09/2025 a 30/09/2025.", [], "e em setembro de 2025?", hoje=QUARTA)
    # mês sem ano: o deste ano
    assert numeros_conferem("De 01/03/2026 a 31/03/2026.", [], "e em março?", hoje=QUARTA)


@pytest.mark.parametrize("pergunta, texto", [
    ("quem foi o melhor vendedor do mês?", "Este mês vai de 01/10/2026 a 31/10/2026; o passado, de 01/09/2026 a 30/09/2026."),
    ("e a semana passada?", "A semana passada foi de 19/10/2026 a 25/10/2026; esta começou em 26/10/2026."),
    ("e este ano?", "Este ano vai de 01/01/2026 a 31/12/2026."),
])
def test_pontas_dos_periodos_relativos_citados_passam(pergunta, texto):
    assert numeros_conferem(texto, [], pergunta, hoje=QUARTA)


def test_periodo_relativo_nao_libera_outras_datas():
    assert numeros_sem_origem("A semana passada foi de 20/10/2026 a 26/10/2026.", [], "e a semana passada?",
                              hoje=QUARTA) == {"20/10/2026"}


def test_data_calculada_pelo_modelo_nao_passa():
    texto = "Vencem em 01/10/2027, então foram ganhos por volta de 01/10/2026."
    resultado = {"proximo_vencimento": [{"dia": "01/10/2027"}]}
    pergunta = "sabendo que os pontos valem por um ano, quando foram ganhos?"
    assert numeros_sem_origem(texto, [resultado], pergunta, "Hoje é 28/10/2026.", QUARTA) == {"01/10/2026"}


def test_data_que_veio_da_ferramenta_passa_inteira_ou_curta():
    resultado = {"periodo": {"de": "19/10/2026", "ate": "25/10/2026"}}
    assert numeros_conferem("De 19/10/2026 a 25/10/2026, ou seja, de 19/10 a 25/10.", [resultado], "e semana passada?")


def test_data_curta_sem_origem_nao_passa():
    # 01/01 é a ponta do ano citado; 30/06 o modelo inventou para dividir o ano
    assert numeros_sem_origem("Posso consultar de 01/01 a 30/06.", [], "e em 2025?", hoje=QUARTA) == {"30/06"}


def test_data_que_nao_existe_e_conferida_como_numero():
    assert numeros_sem_origem("Vendeu em 32/13/2026.", [], "quando?") == {"32", "13", "2026"}
    assert numeros_sem_origem("Foram 45/60 pedidos.", [], "quantos?") == {"45", "60"}


def test_data_nao_libera_valor_inventado():
    assert numeros_sem_origem("Em 29/10/2026 vamos vender R$ 1.500,00.", [], "amanhã?", hoje=QUARTA) == {"1.500,00"}


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


def test_tabela_vai_para_a_tela_mas_nao_para_o_modelo_nem_para_o_historico(conversa):
    tabela = {"titulo": "Maiores clientes", "colunas": ["Posição", "Cliente"],
              "linhas": [{"celulas": ["1º", "Maria Souza"], "url": "/interno/clientes/sem-conta/9"}]}
    conversa.ferramenta = lambda parametros: {"texto": "O 1º comprou R$ 48.230,00.", "tabela": tabela}
    conversa.rodadas = [rodada(chamadas=[VENDAS]), rodada("O 1º comprou R$ 48.230,00. O nome está na tabela.")]

    r = assistente_interno.responder(BancoFalso(), USUARIO, "qual cliente mais comprou?")

    assert r["tabelas"] == [tabela]
    assert "Maria" not in str(conversa.recebido)
    assert "Maria" not in str(conversa.mensagens)


def test_limite_dito_pelo_erro_da_ferramenta_vale_na_resposta(conversa):
    def fora_da_regra(parametros):
        raise RegraDeNegocio("Período maior que um ano (no máximo 366 dias por consulta): divida em partes")
    conversa.ferramenta = fora_da_regra
    conversa.rodadas = [rodada(chamadas=[VENDAS]),
                        rodada("Cada consulta cobre no máximo 366 dias. Quer que eu divida em dois anos?")]

    r = assistente_interno.responder(BancoFalso(), USUARIO, "vendas dos últimos 2 anos")

    assert r["numeros_conferidos"] is True and r["resposta"].startswith("Cada consulta cobre no máximo 366 dias")


def test_numero_de_resposta_anterior_da_conversa_vale_sem_consultar_de_novo(conversa):
    conversa.rodadas = [rodada(chamadas=[VENDAS]), rodada("A Savassi vendeu R$ 48.230,00 em 112 pedidos.")]
    primeira = assistente_interno.responder(BancoFalso(), USUARIO, "quanto a savassi vendeu?")

    # explicar a resposta anterior repete os números dela: já foram conferidos, então passam
    conversa.rodadas = [rodada("Foram 112 pedidos, como eu disse: a conta é só de vendas pagas.")]
    segunda = assistente_interno.responder(BancoFalso(), USUARIO, "como você contou?", primeira["id_conversa"])

    assert segunda["numeros_conferidos"] is True
    assert segunda["resposta"].startswith("Foram 112 pedidos")


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


def test_unidade_do_topo_vai_junto_da_pergunta_e_volta_na_resposta(conversa, monkeypatch):
    monkeypatch.setattr(unidade_repository, "buscar_unidade", lambda db, i: SAVASSI if i == 5 else None)
    monkeypatch.setattr(assistente_interno, "marcas_visiveis", lambda db, u: {"vulto"})
    conversa.rodadas = [rodada("Qual período?")]

    r = assistente_interno.responder(BancoFalso(), USUARIO, "quanto vendemos?", id_unidade_em_foco=5)

    enviada = conversa.recebido[0][-1]["content"]
    assert enviada.startswith("quanto vendemos?\n\n[Tela: unidade Vulto Savassi (id 5) selecionada no topo.")
    assert r["unidade_em_foco"] == {"id_unidade": 5, "nome": "Vulto Savassi"}
    # o histórico guarda só a pergunta, sem o contexto da tela
    assert conversa.mensagens[0]["texto"] == "quanto vendemos?"


@pytest.mark.parametrize("id_unidade, marcas", [(None, {"vulto"}), (5, {"lorenzi"}), (99, {"vulto"})])
def test_sem_unidade_no_topo_ou_de_marca_que_nao_ve_vale_a_rede(conversa, monkeypatch, id_unidade, marcas):
    monkeypatch.setattr(unidade_repository, "buscar_unidade", lambda db, i: SAVASSI if i == 5 else None)
    monkeypatch.setattr(assistente_interno, "marcas_visiveis", lambda db, u: marcas)
    conversa.rodadas = [rodada("Ok.")]

    r = assistente_interno.responder(BancoFalso(), USUARIO, "vendas?", id_unidade_em_foco=id_unidade)

    assert "\"Todas as unidades\" selecionado no topo" in conversa.recebido[0][-1]["content"]
    assert r["unidade_em_foco"] is None


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
    monkeypatch.setattr(assistente_interno, "responder", lambda db, u, texto, id_conversa, em_foco: {
        "id_conversa": 1, "resposta": f"eco: {texto} ({em_foco})", "links": [], "ferramentas": [],
        "numeros_conferidos": None, "unidade_em_foco": {"id_unidade": 5, "nome": "Vulto Savassi"} if em_foco else None})
    try:
        cliente = TestClient(app)
        resposta = cliente.post("/assistente/interno/mensagens", json={"texto": "  oi  ", "id_unidade_em_foco": 5})
        vazia = cliente.post("/assistente/interno/mensagens", json={"texto": "   "})
    finally:
        app.dependency_overrides.clear()

    assert resposta.status_code == 200 and resposta.json()["resposta"] == "eco: oi (5)"
    assert resposta.json()["unidade_em_foco"] == {"id_unidade": 5, "nome": "Vulto Savassi"}
    assert vazia.status_code == 422
