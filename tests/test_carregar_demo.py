from collections import Counter, defaultdict
from datetime import datetime, timezone

from scripts import carregar_demo as demo
from src.utils.cpf import cpf_valido

AGORA = datetime(2026, 10, 6, 15, 0, tzinfo=timezone.utc)

# os 16 códigos da seção 6 do case; os três primeiros são da Gestão, só do Admin
GESTAO = {"gerenciar_contas", "gerenciar_modelos_acesso", "gerenciar_unidades"}
CODIGOS = GESTAO | {
    "gerenciar_catalogo", "movimentar_estoque", "definir_estoque_minimo", "solicitar_transferencia",
    "enviar_transferencia", "receber_transferencia", "registrar_venda_fisica", "preparar_entregar_pedido",
    "cancelar_pedido_equipe", "corrigir_cadastro_cliente", "atender_chamado", "moderar_avaliacoes",
    "registrar_troca_devolucao",
}
TIPO_POR_UNIDADE = {u["nome"]: u["tipo"] for u in demo.UNIDADES}


# ---------- catálogo, modelos e contas ----------

def test_skus_e_combinacoes_de_cor_e_tamanho_sao_unicos():
    variantes = demo.variantes_do_catalogo()

    assert len({v["sku"] for v in variantes}) == len(variantes)
    assert len({(v["produto"], v["cor"], v["tamanho"]) for v in variantes}) == len(variantes)
    assert all(len(v["sku"]) <= 50 for v in variantes)


def test_modelos_seguem_a_tabela_do_case_sem_gestao():
    for codigos in demo.MODELOS.values():
        assert set(codigos) <= CODIGOS
        assert not set(codigos) & GESTAO


def test_clientes_tem_cpf_valido_e_unico():
    cpfs = [c["cpf"] for c in demo.CLIENTES]

    assert all(cpf_valido(cpf) for cpf in cpfs)
    assert len(set(cpfs)) == len(cpfs)


def test_emails_sao_de_dominio_reservado_para_exemplos():
    contas = demo.CONTAS_INTERNAS + demo.CLIENTES

    assert all(c["email"].endswith(".example") for c in contas)
    assert len({c["email"] for c in contas}) == len(contas)


def test_conta_interna_usa_email_corporativo_e_cliente_o_pessoal():
    assert all(c["email"].endswith("@" + demo.DOMINIO_CORPORATIVO) for c in demo.CONTAS_INTERNAS)
    assert all(c["email"].endswith("@" + demo.DOMINIO_PESSOAL) for c in demo.CLIENTES)


def test_funcionario_que_compra_tem_conta_pessoal_separada():
    internas = {c["nome"]: c["email"] for c in demo.CONTAS_INTERNAS}
    pessoais = [c for c in demo.CLIENTES if c["nome"] in internas]

    assert pessoais
    assert all(c["email"] != internas[c["nome"]] for c in pessoais)


def test_tem_uma_conta_de_cada_modelo_inclusive_admin():
    assert sorted(c["modelo"] for c in demo.CONTAS_INTERNAS) == sorted(["Admin", *demo.MODELOS])


def test_estoquista_das_movimentacoes_e_uma_conta_da_demo():
    assert demo.EMAIL_ESTOQUISTA in {c["email"] for c in demo.CONTAS_INTERNAS if c["modelo"] == "Estoquista"}


def test_contas_internas_apontam_para_modelos_e_unidades_da_demo():
    nomes_unidades = {u["nome"] for u in demo.UNIDADES}
    for conta in demo.CONTAS_INTERNAS:
        # o Admin vem da migration; os outros modelos são criados pelo script
        assert conta["modelo"] == "Admin" or conta["modelo"] in demo.MODELOS
        assert conta["unidade"] is None or conta["unidade"] in nomes_unidades


def test_unidades_respeitam_as_regras_do_cd():
    for unidade in demo.UNIDADES:
        assert unidade["tipo"] in ("loja", "cd")
        assert unidade["tipo"] != "cd" or unidade["despacha_online"]
        assert len(unidade["cep"]) == 8 and len(unidade["uf"]) == 2


# ---------- movimentações ----------

def test_saldo_nunca_fica_negativo():
    saldos = defaultdict(int)
    for m in demo.montar_movimentacoes(AGORA):
        saldos[(m["sku"], m["unidade"], m["canal"])] += m["quantidade"]
        assert saldos[(m["sku"], m["unidade"], m["canal"])] >= 0


def test_movimentacoes_em_ordem_e_no_passado():
    datas = [m["criado_em"] for m in demo.montar_movimentacoes(AGORA)]

    assert datas == sorted(datas)
    assert datas[-1] < AGORA
    assert (AGORA - datas[0]).days <= demo.DIAS_DE_HISTORICO


def test_regras_do_banco_sao_respeitadas():
    for m in demo.montar_movimentacoes(AGORA):
        assert m["quantidade"] != 0
        assert m["tipo"] not in ("avaria", "perda", "ajuste") or m["motivo"]
        # o CD só tem o canal online (trigger aplica_movimentacao)
        assert TIPO_POR_UNIDADE[m["unidade"]] != "cd" or m["canal"] == "online"


def test_toda_variante_tem_saldo_inicial_em_cada_unidade_e_canal():
    iniciais = Counter(
        (m["unidade"], m["canal"]) for m in demo.montar_movimentacoes(AGORA) if m["tipo"] == "saldo_inicial"
    )
    total = len(demo.variantes_do_catalogo())

    assert iniciais == {
        ("CD Guarulhos", "online"): total,
        ("Loja Paulista", "loja_fisica"): total, ("Loja Paulista", "online"): total,
        ("Loja Pinheiros", "loja_fisica"): total, ("Loja Pinheiros", "online"): total,
    }


def test_realocacao_sai_e_entra_na_mesma_unidade_e_hora():
    movimentacoes = demo.montar_movimentacoes(AGORA)
    saidas = [m for m in movimentacoes if m["tipo"] == "saida_realocacao"]
    entradas = [m for m in movimentacoes if m["tipo"] == "entrada_realocacao"]

    assert saidas and len(saidas) == len(entradas)
    for saida, entrada in zip(saidas, entradas):
        assert (saida["sku"], saida["unidade"], saida["criado_em"]) == (
            entrada["sku"], entrada["unidade"], entrada["criado_em"]
        )
        assert saida["quantidade"] + entrada["quantidade"] == 0


def test_mesmo_cenario_a_cada_execucao():
    assert demo.montar_movimentacoes(AGORA) == demo.montar_movimentacoes(AGORA)


def test_alguma_variante_termina_abaixo_do_minimo():
    # a Visão Geral precisa de alertas de reposição para mostrar
    saldos = defaultdict(int)
    for m in demo.montar_movimentacoes(AGORA):
        saldos[(m["sku"], m["unidade"], m["canal"])] += m["quantidade"]

    abaixo_loja = [c for c, q in saldos.items() if c[2] == "loja_fisica" and q < demo.MINIMO_LOJA_FISICA]
    abaixo_cd = [c for c, q in saldos.items() if c[1] == "CD Guarulhos" and q < demo.MINIMO_ONLINE_CD]
    assert abaixo_loja and abaixo_cd


def test_vendas_da_demo_so_carregam_uma_vez():
    # já existe pedido no banco: nada é criado (e nenhuma conta é consultada)
    class SessaoComPedido:
        def scalar(self, consulta):
            return 1

    assert demo.carregar_vendas(SessaoComPedido()) == {}


def test_cpf_na_nota_e_valido_e_nao_e_de_cliente_da_demo():
    assert cpf_valido(demo.CPF_NA_NOTA)
    assert demo.CPF_NA_NOTA not in {c["cpf"] for c in demo.CLIENTES}


def test_venda_com_cpf_na_nota_so_carrega_uma_vez():
    class SessaoComPedido:
        def scalar(self, consulta):
            return 1

    assert demo.carregar_venda_com_cpf_na_nota(SessaoComPedido()) is None


def test_vendedor_fica_numa_loja_com_as_permissoes_do_balcao():
    assert set(demo.MODELOS["Vendedor"]) == {
        "registrar_venda_fisica", "registrar_troca_devolucao", "preparar_entregar_pedido"}
    [vendedor] = [c for c in demo.CONTAS_INTERNAS if c["modelo"] == "Vendedor"]
    assert TIPO_POR_UNIDADE[vendedor["unidade"]] == "loja"
