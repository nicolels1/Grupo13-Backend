from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.entities.vendas import Carrinho, Checkout
from src.models.estoque import MovimentacaoEstoque
from src.use_cases import pedidos
from src.use_cases.erros import Conflito, RecursoNaoEncontrado, RegraDeNegocio
from tests.apoio import SessaoFalsa, api, funcionario  # noqa: F401
from tests.apoio_vendas import (  # noqa: F401
    CAMISA, CD, ID_OUTRO, INATIVA, LOJA_FECHADA, LOJA_RJ, LOJA_SP, SessaoVendas, banco, cliente, com_estoque,
    saldo,
)


def entrega(itens=None):
    return Checkout(itens=itens or [{"id_variante": CAMISA, "quantidade": 2}], modalidade="entrega",
                    id_endereco=7).model_dump()


def retirada(id_loja=LOJA_SP, itens=None):
    return Checkout(itens=itens or [{"id_variante": CAMISA, "quantidade": 2}], modalidade="retirada",
                    id_unidade_retirada=id_loja).model_dump()


def movimentacoes(db):
    return [(m.tipo, m.id_unidade, m.canal, m.quantidade) for m in db.adicionados if isinstance(m, MovimentacaoEstoque)]


def pedido_pago(banco, modalidade="retirada"):
    com_estoque(banco, CAMISA, LOJA_SP, "online", 5)
    com_estoque(banco, CAMISA, CD, "online", 5)
    dados = retirada() if modalidade == "retirada" else entrega()
    pedido = pedidos.checkout(SessaoVendas(banco), cliente(), dados)
    cobrado = pedidos.criar_cobranca(SessaoVendas(banco), cliente(), pedido["id_pedido"], "pix")
    pedidos.responder_cobranca(SessaoVendas(banco), cliente(), cobrado["pagamentos"][-1].id_pagamento, True)
    return banco.pedidos[pedido["id_pedido"]]


# ---------- frete e carrinho ----------

@pytest.mark.parametrize("valor, modalidade, frete", [
    ("100.00", "entrega", "19.90"), ("299.00", "entrega", "0.00"), ("100.00", "retirada", "0.00"),
    ("100.00", None, "0.00"),
])
def test_frete(valor, modalidade, frete):
    assert pedidos.calcular_frete(Decimal(valor), modalidade) == Decimal(frete)


def test_variante_repetida_no_carrinho():
    with pytest.raises(ValidationError, match="repetida"):
        Carrinho(itens=[{"id_variante": 1, "quantidade": 1}, {"id_variante": 1, "quantidade": 2}])


def test_entrega_exige_endereco():
    with pytest.raises(ValidationError, match="id_endereco"):
        Checkout(itens=[{"id_variante": 1, "quantidade": 1}], modalidade="entrega")


def test_resumo_do_carrinho(banco):
    com_estoque(banco, CAMISA, LOJA_SP, "online", 3)
    com_estoque(banco, CAMISA, LOJA_RJ, "online", 1)

    resumo = pedidos.resumo_carrinho(SessaoFalsa(), [{"id_variante": CAMISA, "quantidade": 2}])

    assert resumo["valor_itens"] == Decimal("200.00") and resumo["frete_entrega"] == Decimal("19.90")
    assert resumo["total_entrega"] == Decimal("219.90") and resumo["total_retirada"] == Decimal("200.00")
    # a Loja Rio só tem 1: fica de fora da retirada; a Loja Paulista não despacha: sem entrega
    assert [l["id_unidade"] for l in resumo["lojas_retirada"]] == [LOJA_SP]
    assert resumo["entrega_disponivel"] is False


def test_variante_fora_de_venda(banco):
    with pytest.raises(RegraDeNegocio, match="não está à venda"):
        pedidos.resumo_carrinho(SessaoFalsa(), [{"id_variante": INATIVA, "quantidade": 1}])


def test_unidade_desativada_nao_entra(banco):
    com_estoque(banco, CAMISA, LOJA_FECHADA, "online", 9)
    resumo = pedidos.resumo_carrinho(SessaoFalsa(), [{"id_variante": CAMISA, "quantidade": 1}])
    assert resumo["lojas_retirada"] == []


# ---------- escolha da unidade da entrega ----------

def _candidata(id_unidade, tipo, cidade, uf, total, despacha=True):
    return {"id_unidade": id_unidade, "tipo": tipo, "despacha_online": despacha, "cidade": cidade, "uf": uf,
            "total": total}


def test_cd_vem_antes_da_loja():
    escolhida = pedidos.escolher_unidade_entrega(
        [_candidata(1, "loja", "São Paulo", "SP", 50), _candidata(2, "cd", "Recife", "PE", 1)], "São Paulo", "SP")
    assert escolhida["id_unidade"] == 2


def test_loja_da_mesma_cidade_depois_mesmo_estado_depois_maior_estoque():
    lojas = [_candidata(1, "loja", "Campinas", "SP", 90), _candidata(2, "loja", "Sao Paulo", "SP", 5),
             _candidata(3, "loja", "Curitiba", "PR", 200)]
    assert pedidos.escolher_unidade_entrega(lojas, "São Paulo", "SP")["id_unidade"] == 2
    assert pedidos.escolher_unidade_entrega(lojas, "Santos", "SP")["id_unidade"] == 1
    assert pedidos.escolher_unidade_entrega(lojas, "Recife", "PE")["id_unidade"] == 3


def test_loja_que_nao_despacha_nao_entrega():
    assert pedidos.escolher_unidade_entrega([_candidata(1, "loja", "X", "SP", 9, despacha=False)], "X", "SP") is None


# ---------- checkout ----------

def test_checkout_de_entrega_reserva_no_cd(banco):
    com_estoque(banco, CAMISA, CD, "online", 5)
    com_estoque(banco, CAMISA, LOJA_RJ, "online", 9)
    db = SessaoVendas(banco)

    pedido = pedidos.checkout(db, cliente(), entrega())

    assert pedido["id_unidade"] == CD and pedido["status"] == "aguardando_pagamento"
    assert pedido["valor_total"] == Decimal("219.90") and pedido["valor_frete"] == Decimal("19.90")
    assert saldo(banco, CAMISA, CD) == (5, 2)  # reservado, sem mexer na quantidade
    assert movimentacoes(db) == []
    assert pedido["reserva_expira_em"] - pedidos.agora() <= timedelta(minutes=15)
    assert banco.enderecos_entrega[pedido["id_pedido"]].cidade == "Sao Paulo"


def test_checkout_sem_unidade_com_tudo(banco):
    com_estoque(banco, CAMISA, CD, "online", 1)
    with pytest.raises(RegraDeNegocio, match="Nenhum CD ou loja"):
        pedidos.checkout(SessaoVendas(banco), cliente(), entrega())


def test_checkout_respeita_o_reservado(banco):
    com_estoque(banco, CAMISA, LOJA_SP, "online", 3, reservada=2)
    with pytest.raises(RegraDeNegocio, match="não tem todos os itens"):
        pedidos.checkout(SessaoVendas(banco), cliente(), retirada())


def test_retirada_so_em_loja(banco):
    com_estoque(banco, CAMISA, CD, "online", 5)
    with pytest.raises(RegraDeNegocio, match="CD não atende"):
        pedidos.checkout(SessaoVendas(banco), cliente(), retirada(CD))


def test_retirada_sem_frete(banco):
    com_estoque(banco, CAMISA, LOJA_SP, "online", 5)
    pedido = pedidos.checkout(SessaoVendas(banco), cliente(), retirada())
    assert pedido["valor_frete"] == Decimal("0.00") and pedido["endereco_entrega"] is None


def test_endereco_de_outra_pessoa(banco):
    com_estoque(banco, CAMISA, CD, "online", 5)
    with pytest.raises(RecursoNaoEncontrado, match="Endereço"):
        pedidos.checkout(SessaoVendas(banco), cliente(ID_OUTRO), entrega())


# ---------- pagamento (gateway simulado) ----------

def test_pagamento_aprovado_baixa_o_estoque(banco):
    com_estoque(banco, CAMISA, LOJA_SP, "online", 5)
    pedido = pedidos.checkout(SessaoVendas(banco), cliente(), retirada())
    cobrado = pedidos.criar_cobranca(SessaoVendas(banco), cliente(), pedido["id_pedido"], "pix")
    [cobranca] = cobrado["pagamentos"]
    assert cobranca.status == "pendente" and cobranca.valor == Decimal("200.00")
    assert cobranca.id_transacao_gateway.startswith("sim_")
    db = SessaoVendas(banco)

    pago = pedidos.responder_cobranca(db, cliente(), cobranca.id_pagamento, True)

    assert pago["status"] == "pago" and pago["pago_em"] is not None and pago["valor_pago"] == Decimal("200.00")
    assert saldo(banco, CAMISA, LOJA_SP) == (3, 0)
    assert movimentacoes(db) == [("venda", LOJA_SP, "online", -2)]


def test_pagamento_recusado_mantem_a_reserva(banco):
    com_estoque(banco, CAMISA, LOJA_SP, "online", 5)
    pedido = pedidos.checkout(SessaoVendas(banco), cliente(), retirada())
    cobrado = pedidos.criar_cobranca(SessaoVendas(banco), cliente(), pedido["id_pedido"], "cartao_credito")

    recusado = pedidos.responder_cobranca(SessaoVendas(banco), cliente(), cobrado["pagamentos"][0].id_pagamento, False)

    assert recusado["status"] == "aguardando_pagamento" and saldo(banco, CAMISA, LOJA_SP) == (5, 2)


def test_cobranca_respondida_uma_vez(banco):
    pedido = pedido_pago(banco)
    id_pagamento = banco.pagamentos[0].id_pagamento
    with pytest.raises(Conflito):
        pedidos.responder_cobranca(SessaoVendas(banco), cliente(), id_pagamento, True)
    assert pedido.status == "pago"


def test_trocar_de_metodo_recusa_a_cobranca_anterior(banco):
    com_estoque(banco, CAMISA, LOJA_SP, "online", 5)
    pedido = pedidos.checkout(SessaoVendas(banco), cliente(), retirada())
    pedidos.criar_cobranca(SessaoVendas(banco), cliente(), pedido["id_pedido"], "pix")
    cobrado = pedidos.criar_cobranca(SessaoVendas(banco), cliente(), pedido["id_pedido"], "cartao_debito")
    assert [p.status for p in cobrado["pagamentos"]] == ["recusado", "pendente"]


def test_pagamento_depois_da_reserva_vencida_e_estornado(banco):
    com_estoque(banco, CAMISA, LOJA_SP, "online", 5)
    pedido = pedidos.checkout(SessaoVendas(banco), cliente(), retirada())
    cobrado = pedidos.criar_cobranca(SessaoVendas(banco), cliente(), pedido["id_pedido"], "pix")
    banco.pedidos[pedido["id_pedido"]].reserva_expira_em = pedidos.agora() - timedelta(minutes=1)

    resultado = pedidos.responder_cobranca(SessaoVendas(banco), cliente(), cobrado["pagamentos"][0].id_pagamento, True)

    assert resultado["status"] == "cancelado" and resultado["motivo_cancelamento"] == "reserva_vencida"
    assert [(p.tipo, p.status) for p in resultado["pagamentos"]] == [("pagamento", "aprovado"), ("estorno", "aprovado")]
    assert resultado["valor_pago"] == Decimal("0.00")
    assert saldo(banco, CAMISA, LOJA_SP) == (5, 0)


def test_nao_cobra_com_reserva_vencida(banco):
    com_estoque(banco, CAMISA, LOJA_SP, "online", 5)
    pedido = pedidos.checkout(SessaoVendas(banco), cliente(), retirada())
    banco.pedidos[pedido["id_pedido"]].reserva_expira_em = pedidos.agora() - timedelta(seconds=1)
    with pytest.raises(RegraDeNegocio, match="venceu"):
        pedidos.criar_cobranca(SessaoVendas(banco), cliente(), pedido["id_pedido"], "pix")


def test_cobranca_de_outra_pessoa(banco):
    pedido_pago(banco)
    with pytest.raises(RecursoNaoEncontrado):
        pedidos.responder_cobranca(SessaoVendas(banco), cliente(ID_OUTRO), banco.pagamentos[0].id_pagamento, True)


# ---------- cancelamento e reivindicação ----------

def test_cliente_cancela_antes_de_pagar(banco):
    com_estoque(banco, CAMISA, LOJA_SP, "online", 5)
    pedido = pedidos.checkout(SessaoVendas(banco), cliente(), retirada())
    pedidos.criar_cobranca(SessaoVendas(banco), cliente(), pedido["id_pedido"], "pix")

    cancelado = pedidos.cancelar_pelo_cliente(SessaoVendas(banco), cliente(), pedido["id_pedido"])

    assert cancelado["status"] == "cancelado" and cancelado["motivo_cancelamento"] == "cliente"
    assert cancelado["pagamentos"][0].status == "recusado"
    assert saldo(banco, CAMISA, LOJA_SP) == (5, 0)


def test_cliente_nao_cancela_depois_de_pago(banco):
    pedido = pedido_pago(banco)
    with pytest.raises(RegraDeNegocio, match="abra um chamado"):
        pedidos.cancelar_pelo_cliente(SessaoVendas(banco), cliente(), pedido.id_pedido)


def test_reivindicar_compra_da_loja(banco):
    from src.models.vendas import Pedido
    banco.pedidos[90] = Pedido(id_pedido=90, codigo_venda="CLABCD2345", id_cliente=None, id_unidade=LOJA_SP,
                               canal="loja_fisica", status="entregue", valor_frete=0, valor_total=Decimal("10"))

    pedido = pedidos.reivindicar(SessaoVendas(banco), cliente(), "clabcd2345")

    assert pedido["id_cliente"] == cliente().id_usuario
    with pytest.raises(Conflito, match="já está ligada"):
        pedidos.reivindicar(SessaoVendas(banco), cliente(ID_OUTRO), "CLABCD2345")


def test_reivindicar_codigo_inexistente(banco):
    with pytest.raises(RecursoNaoEncontrado):
        pedidos.reivindicar(SessaoVendas(banco), cliente(), "CLNADA")


# ---------- rotas ----------

def test_carrinho_e_publico(api, banco):
    com_estoque(banco, CAMISA, LOJA_SP, "online", 5)
    resposta = api(SessaoFalsa()).post("/carrinho", json={"itens": [{"id_variante": CAMISA, "quantidade": 1}]})
    assert resposta.status_code == 200 and resposta.json()["total_retirada"] == "100.00"


def test_checkout_so_para_cliente(api, banco):
    resposta = api(SessaoFalsa(), usuario=funcionario()).post("/pedidos", json=retirada())
    assert resposta.status_code == 403


def test_codigo_de_venda_sem_letras_ambiguas():
    codigo = pedidos.novo_codigo_venda()
    assert len(codigo) == 10 and codigo.startswith("CL") and not set(codigo[2:]) & set("01IO")
