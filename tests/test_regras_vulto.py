import pytest

# a migração da Vulto tem requirements próprios (migracao_vulto/requirements.txt); sem pandas, pula
pd = pytest.importorskip("pandas")

from migracao_vulto import numeros as n  # noqa: E402
from migracao_vulto import regras as r  # noqa: E402


# ---------- faturamento ----------

@pytest.mark.parametrize(
    "sku",
    ["VLT-CAM-004-BRA-M", "CAM004BM", "vlt cam 004 branco m"],
)
def test_chave_sku_igual_nos_tres_formatos(sku):
    assert r.chave_sku(sku) == "CAM004BM"


def test_chave_sku_desconhecido_e_none():
    assert r.chave_sku("DIVERSO") is None


@pytest.mark.parametrize(
    "status, esperado",
    [("PAGO", "pago"), ("aprovado", "pago"), (None, "pago"), ("CANCELADA", "cancelado"),
     ("cancelado", "cancelado"), ("aguardando", "aguardando"), ("chargeback", "chargeback")],
)
def test_situacao_junta_as_grafias(status, esperado):
    assert r.situacao(status) == esperado


def test_situacao_desconhecida_levanta_erro():
    with pytest.raises(ValueError):
        r.situacao("devolvido")


@pytest.mark.parametrize(
    "obs, ref, valor, pedido",
    [("TESTE QA", None, None, "S100"), (None, "qa.app@vulto.com.br", None, None),
     (None, None, "12345678909", None), (None, "00000000000", None, "APP-9900014")],
)
def test_eh_teste(obs, ref, valor, pedido):
    assert r.eh_teste(obs, ref, valor, pedido)


def test_venda_comum_nao_e_teste():
    assert not r.eh_teste("troca", "cliente@gmail.com", "52998224725", "S100234")


def test_tipo_lancamento():
    assert r.tipo_lancamento(-89.9, "devolucao ref #12") == "devolucao"
    assert r.tipo_lancamento(89.9, "Troca tam") == "troca"
    assert r.tipo_lancamento(89.9, None) == "venda"


def test_canal_pelo_codigo_do_pedido_e_nao_pela_loja():
    assert r.canal_venda("5", "APP-7001340") == "app"
    assert r.canal_venda("5", "S100234") == "site"
    assert r.canal_venda("2", None) == "loja"


def test_mes_brasilia_volta_a_venda_da_noite_para_o_dia_anterior():
    assert r.mes_brasilia("2026-10-01 02:28:31+00:00") == "2026-09"
    assert r.mes_utc("2026-10-01 02:28:31+00:00") == "2026-10"


@pytest.mark.parametrize(
    "valor, esperado",
    [("2026-02-03 00:00:00", "2026-03-02"), ("03/03", "2026-03-03"), ("22/03/2026", "2026-03-22")],
)
def test_data_batel_destroca_dia_e_mes(valor, esperado):
    assert r.data_batel(valor) == esperado


def test_data_batel_formato_desconhecido():
    with pytest.raises(ValueError):
        r.data_batel("março")


def _catalogo():
    return pd.DataFrame({"sku": ["VLT-CAM-004-BRA-M"], "preco": ["100.00"]})


def _vendas(linhas):
    colunas = ["id", "pedido_externo", "loja_id", "cliente_ref", "cliente_valor", "vendedor", "total",
               "desconto", "frete", "status", "criado_em", "obs"]
    df = pd.DataFrame([dict(zip(colunas, linha)) for linha in linhas], dtype=str)
    return df.assign(linha_origem="vendas#" + df["id"])


def test_pedidos_em_centavos_pela_razao_com_o_catalogo():
    vendas = _vendas([("1", "APP-1", "6", None, None, None, "10000", "0", "0", "PAGO", "2025-08-01 12:00:00+00:00", None),
                      ("2", "APP-2", "6", None, None, None, "100", "0", "0", "PAGO", "2025-08-01 12:00:00+00:00", None)])
    itens = pd.DataFrame({"venda_id": ["1", "2"], "sku": ["CAM004BM", "VLT-CAM-004-BRA-M"],
                          "qtd": ["1", "1"], "preco_unit": ["10000", "100"]})
    assert r.pedidos_em_centavos(vendas, itens, _catalogo()) == {"1": True, "2": False}


def test_pedido_do_app_sem_preco_de_catalogo_levanta_erro():
    vendas = _vendas([("1", "APP-1", "6", None, None, None, "10000", "0", "0", "PAGO", "2025-08-01 12:00:00+00:00", None)])
    itens = pd.DataFrame({"venda_id": ["1"], "sku": ["DIVERSO"], "qtd": ["1"], "preco_unit": ["10000"]})
    with pytest.raises(ValueError):
        r.pedidos_em_centavos(vendas, itens, _catalogo())


def test_faturamento_aplica_as_decisoes_e_a_ponte_fecha():
    dia = "2025-08-01 12:00:00+00:00"
    vendas = _vendas([
        ("1", "APP-1", "6", None, None, None, "11990", "0", "0", "PAGO", dia, None),          # centavos + frete 19,90
        ("2", "S1", "5", None, None, None, "114.90", "0", "14.90", "aprovado", dia, None),    # frete na coluna
        ("3", None, "1", None, None, "Ana", "100", None, None, "CANCELADA", dia, None),       # cancelada
        ("4", None, "1", None, None, "Ana", "100", None, None, "pago", dia, "troca tam"),     # troca
        ("5", None, "1", None, None, "Ana", "-100", None, None, "pago", dia, "devolucao"),    # devolução
        ("6", "S1", "5", None, None, None, "114.90", "0", "14.90", "aguardando", "2025-08-01 11:59:00+00:00", None),
        ("7", None, "1", None, None, "Ana", "100", None, None, None, "2026-10-01 01:00:00+00:00", None),  # 30/09
    ])
    itens = pd.DataFrame({"venda_id": ["1", "2", "3", "4", "5", "6", "7"],
                          "sku": ["VLT-CAM-004-BRA-M"] * 7, "qtd": ["1"] * 7,
                          "preco_unit": ["10000", "100", "100", "100", "-100", "100", "100"]})
    lanc = r.lancamentos_banco(vendas, itens, _catalogo())
    fat = lanc.set_index("id")["faturamento"]
    assert fat["1"] == 100.0 and fat["2"] == 100.0  # sem frete (V7) e sem centavos (V1)
    assert fat["3"] == 0 and fat["4"] == 0 and fat["6"] == 0  # cancelada, troca, cópia aguardando (V5)
    assert fat["5"] == -100.0
    assert lanc.set_index("id").loc["7", "mes"] == "2026-09"

    ponte = n.ponte(lanc).set_index("passo")["valor"]
    assert ponte["Nosso faturamento"] == 200.0
    assert ponte["V7 frete à parte"] == -34.8
    assert ponte["V16 fuso: vendas de 30/09 à noite (01/10 em UTC)"] == 100.0


# ---------- passivo de pontos ----------

@pytest.mark.parametrize(
    "valor, esperado",
    [("2.203", 2203), ("1389 pts", 1389), ("15.000", 15000), ("2,7k", 2700), ("15,0k", 15000), (None, 0)],
)
def test_pontos_planilha_le_os_formatos(valor, esperado):
    assert r.pontos_planilha(valor) == esperado


def test_epoca_do_ponto_pela_data_do_credito():
    assert r.epoca("2025-02-28 23:59:00") == "antigo"
    assert r.epoca("2025-03-01") == "novo"


def test_candidatos_um_digito():
    conhecidos = {"52998224725", "11144477735"}
    assert r.candidatos_um_digito("52998224726", conhecidos) == ["52998224725"]
    assert r.candidatos_um_digito("99998224726", conhecidos) == []


def test_pontos_do_resgate_corrige_o_ponto_de_milhar():
    resgates = pd.DataFrame({"linha_origem": ["R!1", "R!2", "R!3"], "pontos usados": ["2.0", None, "500.0"]})
    pendencias = pd.DataFrame({"coluna": ["pontos usados"], "linha": ["R!2"], "valor": ["1.500"]})
    assert r.pontos_do_resgate(resgates, pendencias).tolist() == [2000, 1500, 500]


def _identidades():
    clientes = pd.DataFrame({"linha_origem": ["Clientes!2", "Clientes!3"], "cpf": ["52998224725", "11144477735"],
                             "Tel": ["+5511987654321", None]})
    pendencias = pd.DataFrame({"fonte": ["clientes"], "coluna": ["Tel"], "linha": ["Clientes!3"],
                               "valor": ["91234-5678"], "motivo": ["telefone sem DDD"]})
    return r.Identidades(clientes, pendencias, {"52998224725", "11144477735"})


def test_identidades_acham_o_dono_do_telefone():
    ident = _identidades()
    assert ident.onze_digitos("11987654321", False) == ("52998224725", "J1c telefone do cadastro")
    assert ident.onze_digitos("41912345678", False) == ("11144477735", "J1c celular sem DDD no cadastro")
    assert ident.nove_digitos("987654321") == ("52998224725", "J1d celular sem DDD")


def test_identidades_cpf_digitado_errado_e_preenchimento():
    ident = _identidades()
    assert ident.onze_digitos("52998224726", True) == ("52998224725", "K2 CPF digitado errado")
    assert ident.onze_digitos("00000000000", False) == (None, "K2 CPF de preenchimento")


def test_valorar_fifo_gasta_primeiro_o_ponto_antigo():
    movimentos = pd.DataFrame({
        "cpf": ["A"] * 4,
        "mov": [1000, 500, -1200, -100],
        "quando": ["2025-01-01", "2025-06-01", "2025-07-01", "2025-08-01"],
        "epoca": ["antigo", "novo", "novo", "novo"],
        "fifo": [False, False, True, False],  # o último é um estorno do crédito de junho
    })
    saldo = r.valorar_fifo(movimentos).loc["A"]
    assert (saldo["antigo"], saldo["novo"], saldo["pontos"]) == (0, 200, 200)


def test_valorar_fifo_entrada_cobre_primeiro_o_saldo_negativo():
    # resgatou 800 com 500 no saldo; o crédito seguinte, ainda a R$ 0,10, só paga a dívida (como o trigger)
    movimentos = pd.DataFrame({
        "cpf": ["A"] * 4,
        "mov": [500, -800, 290, 1000],
        "quando": ["2025-01-01", "2025-02-25", "2025-02-26", "2025-07-01"],
        "epoca": ["antigo", "antigo", "antigo", "novo"],
        "fifo": [False, True, False, False],
    })
    saldo = r.valorar_fifo(movimentos).loc["A"]
    assert (saldo["antigo"], saldo["novo"], saldo["pontos"]) == (0, 990, 990)


def test_saldo_inicial_nao_soma_a_aba_antiga_de_quem_tem_o_cpf_corrigido():
    # o ATIVO da aba antiga está na aba nova com o CPF digitado errado: vale só o saldo da aba nova (K5c)
    clientes = pd.DataFrame({"linha_origem": ["Clientes!2"], "cpf": [None], "Pontos": ["108"]})
    antigos = pd.DataFrame({"CPF/CNPJ": ["52998224725"], "PONTOS_2023": ["6798"], "STATUS": ["ATIVO"]})
    pendencias = pd.DataFrame({"fonte": ["clientes"], "coluna": ["cpf"], "linha": ["Clientes!2"],
                               "valor": ["52998224726"]})
    saldo, sem_dono = r.saldo_inicial(clientes, antigos, pendencias, {"52998224725"})
    assert saldo.to_dict() == {"52998224725": 108}
    assert sem_dono == 0


# ---------- clientes ----------

def test_cpfs_conhecidos_so_validos_e_sem_o_de_teste():
    serie = pd.Series(["529.982.247-25", "52998224726", "12345678909", None])
    assert r.cpfs_conhecidos(serie) == {"52998224725"}


def _compradores():
    clientes = pd.DataFrame({"cpf": ["52998224725", "11144477735", "39053344705"],
                             "Tel": ["+5511987654321", None, None],
                             "E-mail": ["ana@x.com", "familia@x.com", "Familia@x.com"]})
    return r.Compradores(clientes)


def test_compradores_numero_pelo_cadastro_e_depois_pelo_formato():
    c = _compradores()
    assert c.identidade("numero", "11987654321") == "52998224725"  # telefone do cadastro (J1b, camada 1)
    assert c.identidade("numero", "11144477735") == "11144477735"  # CPF do cadastro
    assert c.identidade("numero", "41912345678") is None  # só formato de celular: não é cliente
    assert c.identidade("cpf", "12345678909") is None  # CPF de teste (V8)


def test_compradores_email_de_familia_nao_escolhe_cpf():
    c = _compradores()
    assert c.identidade("email", "ANA@x.com") == "52998224725"
    assert c.identidade("email", "familia@x.com") == "familia@x.com"  # P2
    assert c.identidade("email", "novo@x.com") == "novo@x.com"
    c.ponte = {"novo@x.com": "11144477735"}
    assert c.identidade("email", "novo@x.com") == "11144477735"  # J1a


def test_venda_do_atendimento_pelo_codigo_e_pelo_texto():
    vendas = pd.DataFrame({"id": ["1", "2", "3"], "pedido_externo": ["S100234", "APP-7001340", None],
                           "criado_em": ["2026-01-01", "2026-01-01", "2026-01-01"]})
    atendimentos = pd.DataFrame({
        "pedido_codigo": ["S100234", "7001340", None, "3", "3"],
        "mensagem": [None, None, "oi, meu pedido S100234 não chegou [atendente] ok", None, None],
        "data_hora": ["2026-01-10", "2026-01-10", "2026-01-10", "2026-01-10", "2026-06-10"],
    })
    achados = r.venda_do_atendimento(atendimentos, vendas)
    assert achados["id_venda"].tolist() == ["1", "2", "1", "3", None]
    assert achados["como"].tolist() == ["código", "app sem prefixo", "código", "número solto", "código não acha"]


def test_ddd_so_quando_ha_um_so():
    clientes = pd.DataFrame({"Tel": ["+5511987654321", "+5521987654321", "+5531912345678"]})
    vazio = pd.DataFrame({"contato_tipo": [], "contato_valor": [], "cliente_tipo": [], "cliente_valor": []})
    ddd = r.ddd_dos_sem_ddd(clientes, vazio, vazio, vazio)
    assert ddd == {"912345678": "31"}  # 987654321 aparece com dois DDDs: não completa (K9a)


@pytest.mark.parametrize(
    "aceita, obs, esperado",
    [("x", None, "aceita"), ("1", None, "aceita"), ("N", None, "nao_aceita"), (None, None, "nao_informado"),
     ("sim", "Não ligar", "nao_aceita")],
)
def test_consentimento(aceita, obs, esperado):
    assert r.consentimento(aceita, obs) == esperado


def test_consentimento_juntado_recusa_vale_sobre_aceite():
    assert r.consentimento_juntado(["aceita", "nao_aceita"]) is False
    assert r.consentimento_juntado(["nao_informado", "aceita"]) is True
    assert r.consentimento_juntado(["nao_informado"]) is None


# ---------- estoque ----------

@pytest.mark.parametrize(
    "sku",
    ["VLT-VES-006-VIN-M", "VES006HM", "vlt ves 006 vinho m", "VLT VES 006 VINHO M"],
)
def test_sku_oficial_converte_os_tres_formatos(sku):
    assert r.sku_oficial(sku) == "VLT-VES-006-VIN-M"


def test_sku_oficial_usa_a_tabela_de_letras_e_nao_a_inicial():
    assert r.sku_oficial("CAL001V38") == "VLT-CAL-001-OLI-38"  # V = verde oliva, não vinho
    assert r.sku_oficial("TRI012CGG") == "VLT-TRI-012-CIN-GG"


@pytest.mark.parametrize("sku", ["DIVERSO", "VLT-CA-L023-LIL-36", "VLT VES 006 MARROM", "SAI00O3GG", None])
def test_sku_oficial_recusa_o_que_nao_da_para_converter(sku):
    assert r.sku_oficial(sku) is None


@pytest.mark.parametrize(
    "obs, esperado",
    [("2 un -> savassi", ("Vulto Savassi", 2)), ("transf p/ batel 3un", ("Vulto Batel", 3)),
     ("enviado eldorado - 1 pçs", ("Vulto Shopping Eldorado", 1)), ("TRANSF PINHEIROS 4", ("Vulto Pinheiros", 4))],
)
def test_saida_do_cd_le_destino_e_quantidade(obs, esperado):
    assert r.saida_do_cd(obs) == esperado


def test_saida_do_cd_ignora_recebimento():
    assert r.saida_do_cd("recebido do CD romaneio 6") is None
    assert r.saida_do_cd(None) is None


def test_distribuir_pelo_maior_resto_mantem_o_total():
    partes = r.distribuir(10, {"A": 1, "B": 1, "C": 1})
    assert sum(partes.values()) == 10 and sorted(partes.values()) == [3, 3, 4]
    assert r.distribuir(5, {"A": 0}) == {}


def _movimentos(linhas):
    return pd.DataFrame(linhas, columns=["unidade", "sku", "dia", "q"])


def test_posicao_baixa_os_movimentos_depois_da_contagem_e_zera_o_negativo():
    contada = pd.DataFrame({"unidade": ["L", "L"], "sku": ["A", "B"], "q": [5, 1]})
    movimentos = _movimentos([("L", "A", "2026-09-20", -2), ("L", "A", "2026-09-19", -9),
                              ("L", "B", "2026-09-25", -3), ("L", "A", "2026-10-01", -1)])
    p = r.posicao_na_data_base(contada, movimentos, {"L": "2026-09-20"}).set_index("sku")
    assert p.loc["A", "q"] == 3  # a venda do dia da contagem baixa; a de antes e a de outubro não
    assert p.loc["B", "q"] == 0 and p.loc["B", "calculado"] == -2


def test_historico_volta_no_tempo_e_lanca_ajuste_quando_faltaria_peca(monkeypatch):
    monkeypatch.setattr(r, "INICIO_HISTORICO", "2026-09-01")
    contada = pd.DataFrame({"unidade": ["L"], "sku": ["A"], "q": [2]})
    # vendeu 3 em 10/09 e recebeu 6 em 05/09, mas só há 2: das 4 que existiam em 04/09 (2 + 3 − 6 < 0),
    # faltou registrar a saída de 1 peça depois da entrada
    movimentos = _movimentos([("L", "A", "2026-09-10", -3), ("L", "A", "2026-09-05", 6), ("L", "A", "2026-09-03", -2)])
    historico, ajustes = r.reconstruir_historico(contada, movimentos, {"L": "2026-09-20"})
    saldo = historico.set_index("dia")["pecas"]
    assert saldo["2026-09-30"] == 2 and saldo["2026-09-09"] == 5 and saldo["2026-09-04"] == 0
    assert saldo["2026-09-02"] == 2  # desfaz a venda de 03/09
    assert ajustes[["dia", "q"]].values.tolist() == [["2026-09-05", 1.0]]


def test_separar_canal_da_pinheiros_sem_mudar_o_total():
    posicao = pd.DataFrame({"unidade": ["Vulto Pinheiros", "CD Cajamar"], "sku": ["A", "A"], "q": [3, 10]})
    vendidos = pd.DataFrame({"unidade": ["Vulto Pinheiros"] * 2, "sku": ["A", "A"], "q": [-1, -4],
                             "origem": ["venda site", "venda site"]})
    p = r.separar_canal(posicao, vendidos).groupby(["unidade", "canal"])["q"].sum()
    assert p[("Vulto Pinheiros", "online")] == 3 and ("Vulto Pinheiros", "loja_fisica") not in p.index
    assert p[("CD Cajamar", "online")] == 10


def test_codigo_com_dois_candidatos_fica_com_o_que_a_loja_tem_no_sistema_e_nao_contou():
    variantes = {"VLT-BER-007-AZM-40", "VLT-BER-007-AZM-42", "VLT-BER-007-AZM-44"}
    contagem = pd.DataFrame({"unidade": "Pinheiros", "nivel": "sku", "linha_origem": ["a", "b", "c"],
                             "observacao": None, "codigo": ["VLT-BER-007-AZM-40", "VLT-BER-007-AZM-44",
                                                            "VLT-BE-R007-AZM-42"], "quantidade": ["4", "1", "4"]})
    estoque = pd.DataFrame({"loja_id": "1", "sku": sorted(variantes), "qtd": ["4", "3", "3"]})
    itens = pd.DataFrame(columns=["unidade", "sku", "q"])
    c = r.posicao_contada(contagem, estoque, itens, variantes).set_index("linha_origem")
    assert c.loc["c", "sku"] == "VLT-BER-007-AZM-42"
    sem_sistema = r.posicao_contada(contagem, estoque[estoque["sku"] != "VLT-BER-007-AZM-42"], itens, variantes)
    assert sem_sistema.set_index("linha_origem").loc["c", "leitura"] == "ilegível"


def test_saldo_inicial_mais_movimentos_e_ajustes_da_a_posicao(monkeypatch):
    monkeypatch.setattr(r, "INICIO_HISTORICO", "2026-09-01")
    contada = pd.DataFrame({"unidade": ["L"], "sku": ["A"], "q": [2]})
    movimentos = _movimentos([("L", "A", "2026-09-10", -3), ("L", "A", "2026-09-05", 6), ("L", "A", "2026-09-03", -2)])
    posicao = r.posicao_na_data_base(contada, movimentos, {"L": "2026-09-20"})
    _, ajustes = r.reconstruir_historico(contada, movimentos, {"L": "2026-09-20"})
    inicial = r.saldo_inicial_estoque(posicao, movimentos, ajustes)
    assert inicial[("L", "A")] == 2  # o saldo de 02/09 no teste do histórico
    assert inicial[("L", "A")] + movimentos["q"].sum() - ajustes["q"].sum() == posicao["q"].sum()


def test_minimo_do_cd_e_a_saida_do_mes_com_piso():
    cd = r.CD
    movimentos = _movimentos([(cd, "A", "2026-09-10", -5), (cd, "A", "2026-09-20", -4), (cd, "A", "2026-08-31", -9),
                              (cd, "B", "2026-09-15", -1), (cd, "C", "2026-09-15", 3), ("Loja", "D", "2026-09-15", -6)])
    minimo = r.estoque_minimo_cd(movimentos)
    assert minimo.to_dict() == {"A": 9, "B": 2}  # agosto não conta; entrada e loja não têm mínimo


# ---------- vendas: pedidos ----------

def test_codigo_venda_mantem_o_do_site_e_numera_a_loja_fisica():
    assert r.codigo_venda("S100234", "1") == "S100234"
    assert r.codigo_venda(None, "17274") == "VLT-17274"
    assert r.codigo_batel("MARÇO!5") == "VLT-BATEL-5"


def test_vendedor_pela_grafia_sem_acento_e_pela_data():
    assert r.vendedor("1", "Rafa ", "2025-06-01") == "rafael.lima"
    assert r.vendedor("1", "rafa", "2026-01-10") == "rafaela.couto"
    assert r.vendedor("1", "rafa", "2025-11-05") is None  # entre as duas validades, sem dono
    assert r.vendedor("4", "gerente", "2026-03-25") is None


def test_pecas_batel_separa_quantidade_nome_cor_e_tamanho():
    assert r.pecas_batel("2x pochete nylon branco U + moletom zíper college azul marinho M") == [
        (2, "POCHETE NYLON", "BRA", "U"), (1, "MOLETOM ZIPER COLLEGE", "AZM", "M")]

    assert r.vendedor("1", "rafa.pin", "2025-10-31") == "rafael.lima"  # presumido: dia seguinte ao último dele
    assert r.vendedor("1", "Rafa", "2025-11-10") == "rafaela.couto"  # presumido: véspera do primeiro dela

def test_pedido_original_pela_referencia_e_pelo_cliente_e_peca():
    lancamentos = pd.DataFrame({
        "id": ["1", "2", "3", "4"], "tipo": ["venda", "venda", "devolucao", "devolucao"],
        "motivo": [None, None, None, None], "identidade": ["111", "222", "222", "333"],
        "criado_em": ["2026-01-01 12:00:00+00:00", "2026-01-05 12:00:00+00:00", "2026-02-01 12:00:00+00:00",
                      "2026-02-01 12:00:00+00:00"],
        "obs": [None, None, "devolucao ref #1", "dev"]})
    itens = pd.DataFrame({"venda_id": ["1", "2", "3", "4"], "sku": ["CAM004BM"] * 4})
    assert r.pedido_original(lancamentos, itens) == {"3": ("1", "referência na obs")}


def test_ratear_fecha_o_valor_da_venda():
    from migracao_vulto import tratar as t
    pecas = [dict(referencia=100.0, quantidade=2), dict(referencia=50.0, quantidade=1)]
    unitarios = t.ratear(200.0, pecas)
    assert round(unitarios[0] * 2 + unitarios[1], 2) == 200.0


def test_compras_possiveis_unica_no_mesmo_preco_e_depois_presumida_sem_cliente():
    vendidos = pd.DataFrame({
        "id": ["1", "2", "3"], "loja_id": ["1"] * 3, "peca": ["CAM"] * 3, "preco": [89.9, 99.9, 99.9],
        "criado_em": ["2026-01-01", "2026-01-10", "2026-01-12"], "dia": ["2026-01-01", "2026-01-10", "2026-01-12"],
        "identidade": [None, None, "111"]})
    devolucao = pd.Series(dict(loja_id="1", peca="CAM", preco=89.9, criado_em="2026-02-01", dia="2026-02-01"))
    assert r.compras_possiveis(devolucao, vendidos) == [("1", "única compra possível")]
    devolucao["preco"] = 99.9
    assert [i for i, _ in r.compras_possiveis(devolucao, vendidos)][:2] == ["2", "3"]


def test_par_na_planilha_pelo_cliente_e_depois_pelo_valor():
    relancamentos = pd.DataFrame({"id": ["10", "11"], "identidade": ["111", None], "corrigido": [100.0, 50.0]})
    planilha = pd.DataFrame({"linha_origem": ["M!4", "M!5"], "identidade": ["111", None], "valor": [99.0, 50.0]})
    assert r.par_na_planilha(relancamentos, planilha) == {"10": "M!4", "11": "M!5"}


# ---------- fidelidade ----------

def test_venda_da_compra_online_pelo_id_e_loja_fisica_so_quando_unica():
    compras = pd.DataFrame({"id": ["1", "2", "3"], "origem": ["site", "pdv", "pdv"], "cliente_ref": [None, "111", "222"],
                            "valor": ["100", "50", "80"], "loja_id": ["5", "1", "1"],
                            "criado_em": ["2026-01-01 10:00:00+00:00"] * 3})
    vendas = pd.DataFrame({"id": ["10", "20", "30", "31"], "compra_id": ["1.0", None, None, None],
                           "cliente_ref": [None, "111", "222", "222"], "total": [100.0, 50.0, 80.0, 80.0],
                           "loja_id": ["5", "1", "1", "1"], "criado_em": ["2026-01-01 10:00:00+00:00"] * 4})
    # a compra 3 bate com duas vendas iguais: fica sem venda
    assert r.venda_da_compra(compras, vendas).to_dict() == {"1": "10", "2": "20"}


def test_credito_corrigido_liga_a_correcao_do_bug_ao_credito_inflado_do_mesmo_cpf():
    compras = pd.DataFrame({"id": ["1", "2"], "valor": ["13990", "151940"], "origem": ["app", "app"]})
    p = pd.DataFrame({"tipo": ["C", "C", "AJ", "AJ"], "cpf": ["A"] * 4, "compra": ["1", "2", None, None],
                      "q": [13990, 151940, -150421, -13850], "obs": [None, None] + ["correcao bug app v3.4"] * 2})
    assert r.credito_corrigido(p, compras).to_dict() == {2: 1, 3: 0}


def test_passivo_do_extrato_como_o_trigger():
    from migracao_vulto import tratar as t
    mov = pd.DataFrame({
        "cliente": ["A"] * 5,
        "chave": ["pts#1", "pts#2", "pts#3", "pts#4", "pts#5"],
        "pontos": [1000, 500, -300, -1100, 400],
        "movimento_original": [None, None, "pts#2", None, None],  # o estorno sai do crédito de junho
        "criado_em": ["2025-01-01 03:00:00+00:00", "2025-06-01 12:00:00+00:00", "2025-06-02 12:00:00+00:00",
                      "2025-07-01 12:00:00+00:00", "2025-08-01 12:00:00+00:00"],
    })
    passivo = t.passivo_do_extrato(mov).loc["A"]
    # o resgate de 1.100 leva os 1.000 antigos e 100 dos 200 de junho; os 400 de agosto entram inteiros
    assert passivo["saldo"] == 500 and round(passivo["reais"], 2) == 25.0


def test_falas_separa_a_descricao_e_as_falas_seguintes_na_ordem():
    from migracao_vulto.tratar import falas
    descricao, seguintes = falas("Oi, meu pedido S101478 atrasou\n[Atendente]: Vou verificar\n[Cliente]: ok [Bot]: Avalie")
    assert descricao == "Oi, meu pedido S101478 atrasou"
    assert seguintes == [("Atendente", "Vou verificar"), ("Cliente", "ok"), ("Bot", "Avalie")]
    assert falas(None) == ("", [])


def test_origem_sem_linha_levanta_erro():
    from migracao_vulto.tratar import Saida
    saida = Saida()
    saida.de_onde("movimento_pontos", "K5f#123", "vulto.db", "K5f", "K5f")
    assert saida.origem[0]["linha_origem"] == "K5f"
    with pytest.raises(ValueError):
        saida.de_onde("movimentacao_estoque", "mov#1", "contagem", None, "E2")


def test_chaves_dos_clientes_so_usam_o_email_de_quem_nao_tem_cpf():
    from migracao_vulto.tratar import Saida, chaves_dos_clientes
    saida = Saida()
    saida.tabela("cliente_sem_conta", pd.DataFrame({
        "cpf": ["52998224725", "11144477735", None],
        "email": ["familia@ex.com", "familia@ex.com", "so.email@ex.com"]}))
    assert chaves_dos_clientes(saida) == {"52998224725", "11144477735", "so.email@ex.com"}
