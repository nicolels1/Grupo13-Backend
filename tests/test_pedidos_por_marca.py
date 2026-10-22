from decimal import Decimal

from src.use_cases import pedidos
from tests.apoio import SessaoFalsa
from tests.apoio_vendas import (  # noqa: F401
    CAMISA, CD, DUAS_MARCAS, LOJA_SP, banco, com_as_duas_no_estoque, com_estoque, com_vulto,
)

# o carrinho do site junta as duas marcas: o resumo mostra o frete e a entrega de cada uma (ADR 0017)


# ---------- resumo do carrinho ----------

def test_resumo_divide_o_frete_por_marca(com_vulto):
    com_as_duas_no_estoque(com_vulto)

    resumo = pedidos.resumo_carrinho(SessaoFalsa(), DUAS_MARCAS)

    assert [i["marca"] for i in resumo["itens"]] == ["lorenzi", "vulto"]
    # a Casa Lorenzi (R$ 100) paga frete; a Vulto (R$ 350) passa do mínimo e sai grátis
    assert [(m["marca"], m["valor_itens"], m["frete_entrega"], m["entrega_disponivel"]) for m in resumo["marcas"]] == [
        ("lorenzi", Decimal("100.00"), Decimal("19.90"), True),
        ("vulto", Decimal("350.00"), Decimal("0.00"), True),
    ]
    assert resumo["frete_entrega"] == Decimal("19.90") and resumo["total_entrega"] == Decimal("469.90")
    assert resumo["entrega_disponivel"] is True
    # nenhuma loja vende as duas marcas
    assert resumo["lojas_retirada"] == []


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
