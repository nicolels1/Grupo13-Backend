from decimal import Decimal

from src.use_cases import pedidos
from tests.apoio import SessaoFalsa
from tests.apoio_vendas import (  # noqa: F401
    BONE, CAMISA, CD, CD_VULTO, DUAS_MARCAS, LOJA_SP, banco, com_as_duas_no_estoque, com_estoque, com_vulto,
)

# o carrinho do site junta as duas marcas: o resumo mostra a entrega de cada uma e um frete só (ADR 0017)


# ---------- resumo do carrinho ----------

def test_resumo_separa_as_marcas_com_um_frete_so(com_vulto):
    com_as_duas_no_estoque(com_vulto)

    resumo = pedidos.resumo_carrinho(SessaoFalsa(), DUAS_MARCAS)

    assert [i["marca"] for i in resumo["itens"]] == ["lorenzi", "vulto"]
    assert [(m["marca"], m["valor_itens"], m["entrega_disponivel"]) for m in resumo["marcas"]] == [
        ("lorenzi", Decimal("100.00"), True), ("vulto", Decimal("350.00"), True),
    ]
    # o frete grátis conta as duas marcas juntas: R$ 450 em peças
    assert resumo["frete_entrega"] == Decimal("0.00") and resumo["total_entrega"] == Decimal("450.00")
    assert resumo["entrega_disponivel"] is True
    # nenhuma loja vende as duas marcas
    assert resumo["lojas_retirada"] == []


# cada marca sozinha fica abaixo do mínimo, mas o carrinho passa dele: frete grátis
def test_frete_gratis_pela_soma_das_marcas(com_vulto):
    com_estoque(com_vulto, CAMISA, CD, "online", 5)
    com_estoque(com_vulto, BONE, CD_VULTO, "online", 5)
    itens = [{"id_variante": CAMISA, "quantidade": 2}, {"id_variante": BONE, "quantidade": 2}]

    resumo = pedidos.resumo_carrinho(SessaoFalsa(), itens)

    assert [m["valor_itens"] for m in resumo["marcas"]] == [Decimal("200.00"), Decimal("160.00")]
    assert resumo["frete_entrega"] == Decimal("0.00") and resumo["total_entrega"] == Decimal("360.00")


def test_abaixo_do_minimo_paga_um_frete_so(com_vulto):
    com_estoque(com_vulto, CAMISA, CD, "online", 5)
    com_estoque(com_vulto, BONE, CD_VULTO, "online", 5)
    itens = [{"id_variante": CAMISA, "quantidade": 1}, {"id_variante": BONE, "quantidade": 1}]
    resumo = pedidos.resumo_carrinho(SessaoFalsa(), itens)
    assert resumo["frete_entrega"] == Decimal("19.90") and resumo["total_entrega"] == Decimal("199.90")


def test_resumo_sem_entrega_se_uma_marca_nao_tem_estoque(com_vulto):
    com_estoque(com_vulto, CAMISA, CD, "online", 5)
    resumo = pedidos.resumo_carrinho(SessaoFalsa(), DUAS_MARCAS)
    assert [m["entrega_disponivel"] for m in resumo["marcas"]] == [True, False]
    assert resumo["entrega_disponivel"] is False


def test_resumo_de_uma_marca_continua_igual(com_vulto):
    com_estoque(com_vulto, CAMISA, LOJA_SP, "online", 3)
    resumo = pedidos.resumo_carrinho(SessaoFalsa(), [{"id_variante": CAMISA, "quantidade": 2}])
    assert resumo["frete_entrega"] == Decimal("19.90") and resumo["total_entrega"] == Decimal("219.90")
    assert [m["marca"] for m in resumo["marcas"]] == ["lorenzi"]
    assert [l["id_unidade"] for l in resumo["lojas_retirada"]] == [LOJA_SP]
