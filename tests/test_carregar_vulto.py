from decimal import Decimal

import pytest

# a migração da Vulto tem requirements próprios (migracao_vulto/requirements.txt); sem pandas, pula
pd = pytest.importorskip("pandas")

from migracao_vulto import carregar as c  # noqa: E402


def tabela(**colunas) -> pd.DataFrame:
    df = pd.DataFrame(colunas)
    return df.astype(object).where(df.notna(), None)


def chaves(**por_tabela) -> c.Chaves:
    resultado = c.Chaves()
    for nome, mapa in por_tabela.items():
        resultado.guardar(nome, mapa.keys(), mapa.values())
    return resultado


def test_troca_a_chave_pelo_id_e_mantem_o_vazio():
    ids = chaves(unidade={"Vulto Batel": 7})
    assert ids.trocar("unidade", pd.Series(["Vulto Batel", None])) == [7, None]


def test_chave_sem_registro_para_a_carga():
    ids = chaves(unidade={"Vulto Batel": 7})
    with pytest.raises(c.ErroCarga, match="Vulto Savassi"):
        ids.trocar("unidade", pd.Series(["Vulto Savassi"]), "pedido.unidade")


def test_montar_troca_as_ligacoes_tira_as_colunas_de_fora_e_corrige_os_inteiros():
    spec = c.Tabela("item_transferencia", ligacoes={"variante": ("id_variante", "variante")},
                    fora=("obs",), inteiros=("quantidade_recebida",))
    df = tabela(variante=["VLT-A", "VLT-B"], quantidade_recebida=["2.0", None], obs=["x", "y"])

    linhas = c.montar(spec, df, chaves(variante={"VLT-A": 10, "VLT-B": 11}))

    assert list(linhas.columns) == ["id_variante", "quantidade_recebida"]
    assert linhas.to_dict("records") == [dict(id_variante=10, quantidade_recebida=2),
                                         dict(id_variante=11, quantidade_recebida=None)]


# a ordem vem do número, não do texto: "10" depois de "9" (o trigger do saldo aplica nessa ordem)
def test_montar_segue_a_coluna_ordem_como_numero():
    spec = next(t for t in c.TABELAS if t.nome == "movimento_pontos")
    df = tabela(ordem=["10", "9"], chave=["pts#10", "pts#9"], cliente=["123", "123"], pedido=[None, None],
                movimento_original=[None, "pts#10"], registrado_por=[None, None], pontos=["5", "-5"])
    ids = chaves(cliente_sem_conta={"123": 1})

    linhas = c.montar(spec, df, ids, ids=[100, 101])

    assert linhas["pontos"].tolist() == ["-5", "5"]
    assert linhas["id_movimento"].tolist() == [100, 101]
    # o estorno aponta para o id que o crédito recebeu, mesmo vindo antes no CSV
    assert linhas["id_movimento_original"].tolist() == [101, None]


# o estorno precisa do pagamento original já gravado: os originais vão primeiro
def test_pagamento_original_vem_antes_do_estorno():
    spec = next(t for t in c.TABELAS if t.nome == "pagamento")
    df = tabela(chave=["S1#estorno1", "S1#pagamento"], pedido=["S1", "S1"], tipo=["estorno", "pagamento"],
                pagamento_original=["S1#pagamento", None], chamado=[None, None])

    linhas = c.montar(spec, df, chaves(pedido={"S1": 5}), ids=[1, 2])

    assert linhas["tipo"].tolist() == ["pagamento", "estorno"]
    assert linhas["id_pagamento_original"].tolist() == [None, 1]


def test_cliente_e_achado_pelo_cpf_e_sem_cpf_pelo_email():
    df = tabela(cpf=["11144477735", None], email=["a@x.com", "b@x.com"])
    assert c.chave_do_cliente(df).tolist() == ["11144477735", "b@x.com"]


def test_origem_da_migracao_grava_o_id_do_banco():
    df = tabela(tabela=["pedido", "unidade"], id_registro=["S100234", "Vulto Batel"])
    linhas = c.montar_origem(df, chaves(pedido={"S100234": 42}, unidade={"Vulto Batel": 7}))
    assert linhas["id_registro"].tolist() == ["42", "7"]


def test_origem_de_registro_nao_carregado_para_a_carga():
    with pytest.raises(c.ErroCarga, match="S999"):
        c.montar_origem(tabela(tabela=["pedido"], id_registro=["S999"]), chaves(pedido={"S1": 1}))


def test_diferencas_aponta_so_o_numero_que_nao_bateu():
    numeros = dict(c.ESPERADO, **{"estoque em 01/10/2026 (peças)": 15234})
    assert c.diferencas(numeros) == ["estoque em 01/10/2026 (peças)"]
    assert c.diferencas(dict(c.ESPERADO)) == []


@pytest.mark.parametrize("valor, esperado", [(81.0, 81), (float("nan"), None), (2.5, 2.5), ("2.0", "2.0"),
                                             (Decimal("1.10"), Decimal("1.10")), (None, None)])
def test_valor_do_copy(valor, esperado):
    assert c._valor(valor) == esperado
