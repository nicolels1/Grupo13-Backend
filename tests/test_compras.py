import importlib.util
from datetime import timedelta
from types import SimpleNamespace
from decimal import Decimal
from pathlib import Path

import pytest

from src.entities.vendas import CheckoutCompra
from src.use_cases import compras, fidelidade, pedidos, vendas
from src.use_cases.erros import Conflito, RecursoNaoEncontrado, RegraDeNegocio
from tests.apoio import SessaoFalsa, api, funcionario  # noqa: F401
from tests.apoio_vendas import (  # noqa: F401
    CAMISA, CD, CD_VULTO, DUAS_MARCAS, ID_OUTRO, JAQUETA, LOJA_SP, SessaoVendas, banco, cliente,
    com_as_duas_no_estoque, com_estoque, com_vulto, saldo,
)

# compra do site: um checkout e uma cobrança para o cliente, um pedido por marca por dentro (ADR 0017)


def entrega(itens=None, **extra):
    return CheckoutCompra(itens=itens or DUAS_MARCAS, modalidade="entrega", id_endereco=7, **extra).model_dump()


def retirada(itens=None):
    return CheckoutCompra(itens=itens or [{"id_variante": CAMISA, "quantidade": 2}], modalidade="retirada",
                    id_unidade_retirada=LOJA_SP).model_dump()


def compra_das_duas(banco):
    com_as_duas_no_estoque(banco)
    return compras.checkout(SessaoVendas(banco), cliente(), entrega())


def cobrada(banco, metodo="pix"):
    compra = compra_das_duas(banco)
    return compras.criar_cobranca(SessaoVendas(banco), cliente(), compra["id_compra"], metodo)


def vencer(banco, id_compra):
    passou = pedidos.agora() - timedelta(minutes=1)
    banco.compras[id_compra].reserva_expira_em = passou
    for pedido in banco.pedidos.values():
        if pedido.id_compra == id_compra:
            pedido.reserva_expira_em = passou


# programa de pontos de mentira: cada ponto vale R$ 0,10, com as regras de simular_resgate (saldo e desconto
# menor que as peças da marca); o resgate tira o desconto do total do pedido, como o de verdade
@pytest.fixture
def pontos(monkeypatch):
    estado = SimpleNamespace(saldos={"lorenzi": 1000, "vulto": 1000}, resgates=[])

    def simular(db, id_cliente, id_cliente_sem_conta, marca, quantos, valor_itens=None):
        if quantos > estado.saldos[marca]:
            raise RegraDeNegocio(f"Pontos insuficientes: o saldo é de {estado.saldos[marca]} pontos")
        desconto = Decimal(quantos) / 10
        if valor_itens is not None and desconto >= valor_itens:
            raise RegraDeNegocio("O desconto dos pontos precisa ser menor que o valor das peças")
        return {"pontos": quantos, "desconto": desconto, "saldo": estado.saldos[marca], "resgate_minimo": 100}

    def resgatar(db, pedido, marca, quantos, valor_itens):
        desconto = simular(db, pedido.id_cliente, None, marca, quantos, valor_itens)["desconto"]
        estado.resgates.append((pedido.id_unidade, marca, quantos, valor_itens))
        pedido.valor_desconto = desconto
        pedido.valor_total -= desconto
        return desconto

    monkeypatch.setattr(fidelidade, "simular_resgate", simular)
    monkeypatch.setattr(fidelidade, "resgatar", resgatar)
    return estado


# ---------- checkout ----------

def test_checkout_cria_a_compra_com_um_pedido_por_marca(com_vulto):
    compra = compra_das_duas(com_vulto)

    assert compra["status"] == "aguardando_pagamento" and compra["status_cobranca"] is None
    # a Casa Lorenzi (R$ 100) paga frete; a Vulto (R$ 350) passa do mínimo e sai grátis
    assert (compra["valor_itens"], compra["valor_frete"], compra["valor_total"]) == (
        Decimal("450.00"), Decimal("19.90"), Decimal("469.90"))
    assert [(p["id_unidade"], p["valor_total"]) for p in compra["pedidos"]] == [
        (CD, Decimal("119.90")), (CD_VULTO, Decimal("350.00")),
    ]
    # os dois pedidos são da compra, vencem junto com ela e guardam o endereço
    assert all(p["id_compra"] == compra["id_compra"] for p in compra["pedidos"])
    assert {p["reserva_expira_em"] for p in compra["pedidos"]} == {compra["reserva_expira_em"]}
    assert all(com_vulto.enderecos_entrega[p["id_pedido"]].cidade == "Sao Paulo" for p in compra["pedidos"])
    # cada pedido reserva só as peças da sua marca, na sua unidade
    assert saldo(com_vulto, CAMISA, CD) == (5, 1) and saldo(com_vulto, JAQUETA, CD_VULTO) == (5, 1)
    assert compra["codigo_compra"].startswith("CP")


def test_compra_de_uma_marca_pode_retirar_na_loja(com_vulto):
    com_estoque(com_vulto, CAMISA, LOJA_SP, "online", 5)

    compra = compras.checkout(SessaoVendas(com_vulto), cliente(), retirada())

    assert [(p["id_unidade"], p["modalidade"], p["valor_frete"]) for p in compra["pedidos"]] == [
        (LOJA_SP, "retirada", Decimal("0.00"))]
    assert compra["valor_total"] == Decimal("200.00")


# o resgate continua o de sempre: o desconto sai do total do pedido, e a compra cobra a soma já com desconto
def test_compra_de_uma_marca_usa_pontos(com_vulto, pontos):
    com_estoque(com_vulto, CAMISA, LOJA_SP, "online", 5)
    dados = {**retirada(), "pontos_por_marca": {"lorenzi": 200}}

    compra = compras.checkout(SessaoVendas(com_vulto), cliente(), dados)

    assert pontos.resgates == [(LOJA_SP, "lorenzi", 200, Decimal("200.00"))]
    assert (compra["valor_desconto"], compra["valor_total"]) == (Decimal("20.00"), Decimal("180.00"))


# cada marca tem o seu programa: os pontos da Casa Lorenzi descontam do pedido dela, e os da Vulto, do da Vulto
def test_cada_marca_usa_os_seus_pontos_no_pedido_dela(com_vulto, pontos):
    com_as_duas_no_estoque(com_vulto)

    compra = compras.checkout(SessaoVendas(com_vulto), cliente(),
                              entrega(pontos_por_marca={"lorenzi": 200, "vulto": 1000}))

    assert pontos.resgates == [
        (CD, "lorenzi", 200, Decimal("100.00")), (CD_VULTO, "vulto", 1000, Decimal("350.00")),
    ]
    assert [(p["id_unidade"], p["valor_desconto"], p["valor_total"]) for p in compra["pedidos"]] == [
        (CD, Decimal("20.00"), Decimal("99.90")), (CD_VULTO, Decimal("100.00"), Decimal("250.00")),
    ]
    assert (compra["valor_desconto"], compra["valor_total"]) == (Decimal("120.00"), Decimal("349.90"))

    # a cobrança única é o total já com os dois descontos, e cada pedido recebe o valor dele
    compras.criar_cobranca(SessaoVendas(com_vulto), cliente(), compra["id_compra"], "pix")
    compras.responder_cobranca(SessaoVendas(com_vulto), cliente(), compra["id_compra"], True)
    assert [p.valor for p in com_vulto.pagamentos] == [Decimal("99.90"), Decimal("250.00")]


def test_pontos_de_marca_fora_do_carrinho(com_vulto, pontos):
    com_estoque(com_vulto, CAMISA, LOJA_SP, "online", 5)
    dados = {**retirada(), "pontos_por_marca": {"vulto": 200}}
    with pytest.raises(RegraDeNegocio, match="pontos da Vulto só valem com peças da Vulto"):
        compras.checkout(SessaoVendas(com_vulto), cliente(), dados)
    assert com_vulto.compras == {} and pontos.resgates == []


# os pontos de todas as marcas são conferidos antes de reservar: se uma falha, nada é reservado nem resgatado
def test_pontos_insuficientes_numa_marca_nao_reserva_nada(com_vulto, pontos):
    com_as_duas_no_estoque(com_vulto)
    pontos.saldos["vulto"] = 50

    with pytest.raises(RegraDeNegocio, match="Peças da Vulto: Pontos insuficientes"):
        compras.checkout(SessaoVendas(com_vulto), cliente(), entrega(pontos_por_marca={"lorenzi": 200, "vulto": 100}))

    assert saldo(com_vulto, CAMISA, CD) == (5, 0) and saldo(com_vulto, JAQUETA, CD_VULTO) == (5, 0)
    assert com_vulto.compras == {} and pontos.resgates == []


def test_desconto_dos_pontos_menor_que_as_pecas_da_marca(com_vulto, pontos):
    com_as_duas_no_estoque(com_vulto)
    # 1000 pontos valem R$ 100: cobrem a camisa inteira da Casa Lorenzi (R$ 100), que não pode sair de graça
    with pytest.raises(RegraDeNegocio, match="Peças da Casa Lorenzi: O desconto dos pontos precisa ser menor"):
        compras.checkout(SessaoVendas(com_vulto), cliente(), entrega(pontos_por_marca={"lorenzi": 1000}))


def test_duas_marcas_nao_retiram_na_loja(com_vulto):
    com_as_duas_no_estoque(com_vulto)
    dados = CheckoutCompra(itens=DUAS_MARCAS, modalidade="retirada", id_unidade_retirada=LOJA_SP).model_dump()
    with pytest.raises(RegraDeNegocio, match="Retirada na loja só com peças de uma marca"):
        compras.checkout(SessaoVendas(com_vulto), cliente(), dados)


def test_marca_sem_estoque_nao_reserva_nada(com_vulto):
    com_estoque(com_vulto, CAMISA, CD, "online", 5)

    with pytest.raises(RegraDeNegocio, match="Peças da Vulto"):
        compras.checkout(SessaoVendas(com_vulto), cliente(), entrega())

    # a Casa Lorenzi tinha estoque, mas nada foi reservado nem gravado
    assert saldo(com_vulto, CAMISA, CD) == (5, 0)
    assert com_vulto.compras == {} and com_vulto.pedidos == {}


# ---------- cobrança ----------

def test_cobranca_unica_com_o_total_da_compra(com_vulto):
    compra = cobrada(com_vulto)

    assert (compra["metodo"], compra["status_cobranca"]) == ("pix", "pendente")
    assert compra["id_transacao_gateway"].startswith("sim_")
    # o pagamento de cada pedido só nasce na aprovação
    assert com_vulto.pagamentos == []


def test_pedir_a_cobranca_de_novo_devolve_a_mesma(com_vulto):
    primeira = cobrada(com_vulto)
    db = SessaoVendas(com_vulto)

    de_novo = compras.criar_cobranca(db, cliente(), primeira["id_compra"], "pix")

    assert de_novo["id_transacao_gateway"] == primeira["id_transacao_gateway"]
    assert db.commits == 0


def test_trocar_de_metodo_gera_outra_transacao(com_vulto):
    pix = cobrada(com_vulto)
    cartao = compras.criar_cobranca(SessaoVendas(com_vulto), cliente(), pix["id_compra"], "cartao_credito")
    assert (cartao["metodo"], cartao["status_cobranca"]) == ("cartao_credito", "pendente")
    assert cartao["id_transacao_gateway"] != pix["id_transacao_gateway"]


def test_nao_cobra_com_reserva_vencida(com_vulto):
    compra = compra_das_duas(com_vulto)
    vencer(com_vulto, compra["id_compra"])
    with pytest.raises(RegraDeNegocio, match="venceu"):
        compras.criar_cobranca(SessaoVendas(com_vulto), cliente(), compra["id_compra"], "pix")


def test_cobranca_de_outra_pessoa(com_vulto):
    compra = compra_das_duas(com_vulto)
    with pytest.raises(RecursoNaoEncontrado):
        compras.criar_cobranca(SessaoVendas(com_vulto), cliente(ID_OUTRO), compra["id_compra"], "pix")
    with pytest.raises(RecursoNaoEncontrado):
        compras.detalhar_do_cliente(SessaoVendas(com_vulto), cliente(ID_OUTRO), compra["id_compra"])


# ---------- resposta do gateway ----------

def test_aprovada_paga_os_dois_pedidos_e_baixa_o_estoque(com_vulto):
    compra = cobrada(com_vulto, "cartao_credito")

    paga = compras.responder_cobranca(SessaoVendas(com_vulto), cliente(), compra["id_compra"], True)

    assert (paga["status"], paga["status_cobranca"]) == ("pago", "aprovado") and paga["pago_em"] is not None
    assert all(p["status"] == "pago" and p["valor_pago"] == p["valor_total"] for p in paga["pedidos"])
    # um pagamento aprovado por pedido, com o valor dele: o trigger de pontos credita cada marca uma vez
    assert [(p.id_pedido, p.tipo, p.status, p.metodo, p.valor, p.origem) for p in com_vulto.pagamentos] == [
        (paga["pedidos"][0]["id_pedido"], "pagamento", "aprovado", "cartao_credito", Decimal("119.90"), None),
        (paga["pedidos"][1]["id_pedido"], "pagamento", "aprovado", "cartao_credito", Decimal("350.00"), None),
    ]
    assert len({p.id_transacao_gateway for p in com_vulto.pagamentos}) == 2
    # a reserva vira venda nas duas unidades
    assert saldo(com_vulto, CAMISA, CD) == (4, 0) and saldo(com_vulto, JAQUETA, CD_VULTO) == (4, 0)


def test_cobranca_respondida_uma_vez(com_vulto):
    compra = cobrada(com_vulto)
    compras.responder_cobranca(SessaoVendas(com_vulto), cliente(), compra["id_compra"], True)

    with pytest.raises(Conflito, match="já foi respondida"):
        compras.responder_cobranca(SessaoVendas(com_vulto), cliente(), compra["id_compra"], True)

    assert len(com_vulto.pagamentos) == 2
    assert saldo(com_vulto, CAMISA, CD) == (4, 0)


def test_sem_cobranca_nao_ha_o_que_responder(com_vulto):
    compra = compra_das_duas(com_vulto)
    with pytest.raises(Conflito):
        compras.responder_cobranca(SessaoVendas(com_vulto), cliente(), compra["id_compra"], True)


def test_recusada_mantem_a_reserva_para_tentar_de_novo(com_vulto):
    compra = cobrada(com_vulto, "cartao_debito")

    recusada = compras.responder_cobranca(SessaoVendas(com_vulto), cliente(), compra["id_compra"], False)

    assert (recusada["status"], recusada["status_cobranca"]) == ("aguardando_pagamento", "recusado")
    assert all(p["status"] == "aguardando_pagamento" for p in recusada["pedidos"])
    assert saldo(com_vulto, CAMISA, CD) == (5, 1) and com_vulto.pagamentos == []
    nova = compras.criar_cobranca(SessaoVendas(com_vulto), cliente(), compra["id_compra"], "cartao_debito")
    assert nova["status_cobranca"] == "pendente"


# expiração e aprovação na mesma transação: a resposta que chega depois do prazo não cobra nada
def test_aprovacao_depois_da_reserva_vencida_cancela_sem_cobrar(com_vulto):
    compra = cobrada(com_vulto)
    vencer(com_vulto, compra["id_compra"])

    resultado = compras.responder_cobranca(SessaoVendas(com_vulto), cliente(), compra["id_compra"], True)

    assert (resultado["status"], resultado["status_cobranca"]) == ("cancelado", "recusado")
    assert [(p["status"], p["motivo_cancelamento"]) for p in resultado["pedidos"]] == [
        ("cancelado", "reserva_vencida")] * 2
    assert com_vulto.pagamentos == []
    assert saldo(com_vulto, CAMISA, CD) == (5, 0) and saldo(com_vulto, JAQUETA, CD_VULTO) == (5, 0)


# ---------- cancelamento ----------

def test_cliente_cancela_a_compra_inteira(com_vulto):
    compra = cobrada(com_vulto)

    cancelada = compras.cancelar_pelo_cliente(SessaoVendas(com_vulto), cliente(), compra["id_compra"])

    assert (cancelada["status"], cancelada["status_cobranca"]) == ("cancelado", "recusado")
    assert [(p["status"], p["motivo_cancelamento"]) for p in cancelada["pedidos"]] == [("cancelado", "cliente")] * 2
    assert saldo(com_vulto, CAMISA, CD) == (5, 0) and saldo(com_vulto, JAQUETA, CD_VULTO) == (5, 0)


def test_compra_paga_nao_cancela_pelo_site(com_vulto):
    compra = cobrada(com_vulto)
    compras.responder_cobranca(SessaoVendas(com_vulto), cliente(), compra["id_compra"], True)
    with pytest.raises(RegraDeNegocio, match="abra um chamado"):
        compras.cancelar_pelo_cliente(SessaoVendas(com_vulto), cliente(), compra["id_compra"])


def test_pedido_da_compra_nao_paga_nem_cancela_sozinho(com_vulto):
    compra = compra_das_duas(com_vulto)
    id_pedido = compra["pedidos"][0]["id_pedido"]
    with pytest.raises(RegraDeNegocio, match="pague ou cancele pela compra"):
        pedidos.criar_cobranca(SessaoVendas(com_vulto), cliente(), id_pedido, "pix")
    with pytest.raises(RegraDeNegocio, match="pague ou cancele pela compra"):
        pedidos.cancelar_pelo_cliente(SessaoVendas(com_vulto), cliente(), id_pedido)
    assert com_vulto.pagamentos == [] and saldo(com_vulto, CAMISA, CD) == (5, 1)


# a cobrança é uma só: a equipe cancelar um pedido ainda sem pagamento cancela a compra inteira
def test_equipe_cancela_pedido_da_compra_sem_pagamento(com_vulto):
    compra = cobrada(com_vulto)

    vendas.cancelar(SessaoVendas(com_vulto), funcionario(), compra["pedidos"][0]["id_pedido"], "sem estoque físico")

    assert com_vulto.compras[compra["id_compra"]].status == "cancelado"
    assert [(p.status, p.motivo_cancelamento, p.justificativa_cancelamento) for p in com_vulto.pedidos.values()] == [
        ("cancelado", "equipe", "sem estoque físico")] * 2
    assert saldo(com_vulto, CAMISA, CD) == (5, 0) and saldo(com_vulto, JAQUETA, CD_VULTO) == (5, 0)


# depois de pago, cada pedido segue sozinho: troca, devolução e cancelamento são por pedido, como sempre
def test_equipe_cancela_um_pedido_pago_da_compra(com_vulto):
    compra = cobrada(com_vulto)
    compras.responder_cobranca(SessaoVendas(com_vulto), cliente(), compra["id_compra"], True)
    lorenzi, vulto = compra["pedidos"]

    vendas.cancelar(SessaoVendas(com_vulto), funcionario(), lorenzi["id_pedido"], "cliente desistiu")

    assert com_vulto.pedidos[lorenzi["id_pedido"]].status == "cancelado"
    assert com_vulto.pedidos[vulto["id_pedido"]].status == "pago"
    assert com_vulto.compras[compra["id_compra"]].status == "pago"
    estornos = [p for p in com_vulto.pagamentos if p.tipo == "estorno"]
    assert [(p.id_pedido, p.valor) for p in estornos] == [(lorenzi["id_pedido"], Decimal("119.90"))]
    assert saldo(com_vulto, CAMISA, CD) == (5, 0) and saldo(com_vulto, JAQUETA, CD_VULTO) == (4, 0)


# ---------- rotas ----------

# o banco falso não preenche o criado_em (o banco de verdade preenche): aqui só a ligação das rotas
SAIDA = {
    "id_compra": 5, "codigo_compra": "CPABCDEFGH", "status": "aguardando_pagamento", "valor_itens": "450.00",
    "valor_frete": "19.90", "valor_desconto": "0.00", "valor_total": "469.90",
    "reserva_expira_em": "2026-10-22T12:00:00Z",
    "metodo": None, "id_transacao_gateway": None, "status_cobranca": None, "pago_em": None, "cancelado_em": None,
    "criado_em": "2026-10-22T11:45:00Z", "pedidos": [],
}


def test_rotas_chamam_a_compra(api, monkeypatch):
    chamadas = []
    for nome in ("checkout", "detalhar_do_cliente", "criar_cobranca", "responder_cobranca", "cancelar_pelo_cliente"):
        monkeypatch.setattr(compras, nome, lambda *args, nome=nome: chamadas.append((nome, args[2:])) or SAIDA)
    cliente_api = api(SessaoFalsa(), usuario=cliente())

    respostas = [
        cliente_api.post("/compras", json=entrega()),
        cliente_api.get("/compras/5"),
        cliente_api.post("/compras/5/pagamentos", json={"metodo": "pix"}),
        cliente_api.post("/compras/5/simular", json={"aprovado": True}),
        cliente_api.post("/compras/5/cancelar"),
    ]

    assert [r.status_code for r in respostas] == [201, 200, 201, 200, 200]
    assert respostas[0].json()["valor_total"] == "469.90"
    assert [nome for nome, _ in chamadas] == [
        "checkout", "detalhar_do_cliente", "criar_cobranca", "responder_cobranca", "cancelar_pelo_cliente"]
    assert chamadas[0][1][0]["itens"] == DUAS_MARCAS
    assert chamadas[2][1] == (5, "pix") and chamadas[3][1] == (5, True) and chamadas[4][1] == (5,)


def test_rota_recusa_retirada_com_duas_marcas(api, com_vulto):
    com_as_duas_no_estoque(com_vulto)
    dados = CheckoutCompra(itens=DUAS_MARCAS, modalidade="retirada", id_unidade_retirada=LOJA_SP).model_dump()
    resposta = api(SessaoVendas(com_vulto), usuario=cliente()).post("/compras", json=dados)
    assert resposta.status_code == 422 and "uma marca" in resposta.json()["detail"]


def test_rota_responde_409_na_cobranca_ja_respondida(api, com_vulto):
    compra = cobrada(com_vulto)
    compras.responder_cobranca(SessaoVendas(com_vulto), cliente(), compra["id_compra"], True)
    resposta = api(SessaoVendas(com_vulto), usuario=cliente()).post(
        f"/compras/{compra['id_compra']}/simular", json={"aprovado": True})
    assert resposta.status_code == 409


def test_rotas_so_para_cliente(api):
    resposta = api(SessaoFalsa(), usuario=funcionario()).post("/compras", json=entrega())
    assert resposta.status_code == 403


def test_por_marca_saiu_da_api(api):
    resposta = api(SessaoFalsa(), usuario=cliente()).post("/pedidos/por-marca", json=entrega())
    assert resposta.status_code in (404, 405)


# ---------- migration ----------

PASTA = Path(__file__).resolve().parent.parent / "migrations" / "versions"


def _migration(nome):
    especificacao = importlib.util.spec_from_file_location(nome, next(PASTA.glob(f"{nome}_*.py")))
    modulo = importlib.util.module_from_spec(especificacao)
    especificacao.loader.exec_module(modulo)
    return modulo


def test_migration_libera_a_compra_so_para_a_api():
    texto = next(PASTA.glob("8834803e1dc8_*.py")).read_text(encoding="utf-8")
    assert "ENABLE ROW LEVEL SECURITY" in texto
    assert "GRANT SELECT, INSERT, UPDATE ON compra TO {PAPEL}" in texto
    assert "GRANT USAGE, SELECT ON SEQUENCE compra_id_compra_seq TO {PAPEL}" in texto
    assert "CREATE POLICY {POLITICA} ON compra" in texto


# o pg_cron trava a compra antes dos pedidos, como a API, e só vence pedido de compra que ele travou
def test_migration_vence_a_compra_antes_dos_pedidos():
    funcao = _migration("8834803e1dc8").CANCELA_VENCIDOS
    trava_compra = funcao.index("FROM compra")
    trava_pedido = funcao.index("FROM pedido")
    assert trava_compra < trava_pedido
    assert "NOT c.id_compra = ANY(compras)" in funcao
    assert "UPDATE compra SET status = 'cancelado'" in funcao


def test_migration_desfaz_a_funcao_do_pg_cron():
    nova = _migration("8834803e1dc8")
    anterior = _migration("221981ca8429")
    assert nova.CANCELA_VENCIDOS_ANTIGA == anterior.CANCELA_VENCIDOS
    assert nova.down_revision == "c7d2e9a41f60"
