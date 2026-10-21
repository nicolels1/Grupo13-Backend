import pytest

pytest.importorskip("pandas")

from migracao_vulto.regras import de_para  # noqa: E402

# chave de cada tabela: o valor da Vulto que ela traduz (não pode repetir)
CHAVES = {
    "unidade": ["loja_id"], "cor": ["codigo"], "cor_texto": ["texto_vulto"], "status_venda": ["valor_vulto"],
    "conta_interna": ["conta"], "vendedor": ["loja_id", "grafia", "valido_desde"], "atendente": ["grafia"],
    "metodo_pagamento": ["valor_vulto"], "consentimento": ["valor_vulto"],
    "loja_cadastro": ["valor_vulto"], "categoria": ["codigo_sku"], "canal_atendimento": ["valor_vulto"],
    "categoria_chamado": ["tag"], "status_chamado": ["valor_vulto"],
}


@pytest.mark.parametrize("tabela", sorted(CHAVES))
def test_toda_linha_tem_decisao_e_a_chave_nao_repete(tabela):
    t = de_para(tabela)
    assert (t["decisao"] != "").all()
    assert not t.duplicated(CHAVES[tabela]).any()


def test_contas_citadas_existem():
    contas = set(de_para("conta_interna")["conta"])
    citadas = set(de_para("vendedor")["conta"]) | set(de_para("atendente")["conta"])
    assert citadas - {""} <= contas


def test_vendedor_dividido_por_data_nao_se_sobrepoe():
    v = de_para("vendedor")
    rafa = v[v["grafia"] == "rafa"]
    assert rafa["valido_ate"].max() < rafa["valido_desde"].max()


def test_destinos_sao_valores_que_a_lorenzi_aceita():
    assert set(de_para("metodo_pagamento")["metodo"]) <= {"pix", "cartao_credito", "cartao_debito", "dinheiro", "nao_informado"}
    assert set(de_para("status_venda")["status_lorenzi"]) <= {"entregue", "cancelado"}
    migra = de_para("categoria_chamado").query("migra == 'sim'")
    assert set(migra["categoria"]) <= {"entrega", "troca_devolucao", "estorno", "duvida", "outros", "pagamento"}
    assert set(de_para("status_chamado")["situacao"]) == {"concluido", "em_aberto"}
    assert set(de_para("canal_atendimento")["canal"]) == {"whatsapp", "instagram", "email"}
    assert set(de_para("loja_cadastro")["loja_id"]) <= set(de_para("unidade")["loja_id"])
    assert set(de_para("cor")["letra_curta"]).isdisjoint({""})
