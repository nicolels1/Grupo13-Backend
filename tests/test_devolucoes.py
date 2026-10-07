from datetime import timedelta
from decimal import Decimal

import pytest

from src.models.atendimento import Chamado
from src.models.estoque import MovimentacaoEstoque
from src.models.vendas import Pagamento
from src.repositories import atendimento_repository, pedido_repository
from src.use_cases import devolucoes, pedidos, vendas
from src.use_cases.erros import RecursoNaoEncontrado, RegraDeNegocio
from tests.apoio import funcionario
from tests.apoio_vendas import (  # noqa: F401
    CAMISA, CALCA, CD, ID_CLIENTE, LOJA_RJ, SessaoVendas, banco, com_estoque, saldo,
)
from tests.test_pedidos import pedido_pago


@pytest.fixture
def entregue(banco, monkeypatch):
    """Pedido de retirada pago (2 camisas, R$ 200 no Pix), entregue e com chamado de troca ou devolução."""
    pedido = pedido_pago(banco)
    vendas.marcar_pronto_para_retirada(SessaoVendas(banco), pedido.id_pedido)
    vendas.entregar(SessaoVendas(banco), pedido.id_pedido, pedido.codigo_venda)
    chamado = Chamado(id_chamado=8, id_cliente=ID_CLIENTE, id_pedido=pedido.id_pedido, categoria="troca_devolucao",
                      status="em_andamento", assunto="a", descricao="d")
    banco.movimentos_de_chamado = []

    class SessaoDoChamado(SessaoVendas):
        def flush(self):
            super().flush()
            for m in self.adicionados:
                if isinstance(m, MovimentacaoEstoque) and m.id_chamado and m not in banco.movimentos_de_chamado:
                    banco.movimentos_de_chamado.append(m)

    def trocas_e_devolucoes(db, id_pedido):
        somas = {}
        for m in banco.movimentos_de_chamado:
            somas[(m.tipo, m.id_variante)] = somas.get((m.tipo, m.id_variante), 0) + m.quantidade
        return [(tipo, v, soma) for (tipo, v), soma in somas.items()]

    monkeypatch.setattr(atendimento_repository, "travar_chamado", lambda db, i: chamado if i == 8 else None)
    monkeypatch.setattr(pedido_repository, "trocas_e_devolucoes", trocas_e_devolucoes)
    com_estoque(banco, CAMISA, LOJA_RJ, "loja_fisica", 0)
    return Cenario(SessaoDoChamado, banco, pedido, chamado)


class Cenario:
    """Pedido entregue, chamado e uma sessão nova a cada ação, como numa requisição."""

    def __init__(self, classe, banco, pedido, chamado):
        self.classe, self.banco, self.pedido, self.chamado = classe, banco, pedido, chamado

    def nova(self):
        return self.classe(self.banco)

    @property
    def pix(self) -> Pagamento:
        return next(p for p in self.banco.pagamentos if p.tipo == "pagamento" and p.status == "aprovado")


def devolucao(entregue, quantidade=1, valor="100.00", loja=LOJA_RJ):
    return {"id_unidade": loja, "itens": [{"id_variante": CAMISA, "quantidade": quantidade}],
            "estornos": [{"id_pagamento": entregue.pix.id_pagamento, "valor": Decimal(valor)}]}


def test_devolucao_parcial_entra_na_loja_fisica_com_estorno(entregue):
    db = entregue.nova()

    pedido = devolucoes.registrar_devolucao(db, funcionario(), 8, devolucao(entregue))

    assert pedido["status"] == "entregue" and pedido["devolucao"] == "parcial"
    assert pedido["valor_pago"] == Decimal("100.00")
    [estorno] = [p for p in pedido["pagamentos"] if p.tipo == "estorno"]
    assert (estorno.id_chamado, estorno.metodo, estorno.status) == (8, "pix", "aprovado")
    assert saldo(entregue.banco, CAMISA, LOJA_RJ, "loja_fisica") == (1, 0)


def test_devolver_tudo_marca_total(entregue):
    devolucoes.registrar_devolucao(entregue.nova(), funcionario(), 8, devolucao(entregue))
    pedido = devolucoes.registrar_devolucao(entregue.nova(), funcionario(), 8, devolucao(entregue))
    assert pedido["devolucao"] == "total" and pedido["valor_pago"] == Decimal("0.00")


def test_nao_devolve_mais_do_que_comprou(entregue):
    devolucoes.registrar_devolucao(entregue.nova(), funcionario(), 8, devolucao(entregue, quantidade=2, valor="200"))
    with pytest.raises(RegraDeNegocio, match="não tem 1 peça"):
        devolucoes.registrar_devolucao(entregue.nova(), funcionario(), 8, devolucao(entregue, valor="1"))


def test_estorno_nao_passa_do_pago(entregue):
    with pytest.raises(RegraDeNegocio, match="Estorno maior"):
        devolucoes.registrar_devolucao(entregue.nova(), funcionario(), 8, devolucao(entregue, valor="250.00"))


def test_prazo_de_30_dias(entregue):
    entregue.pedido.entregue_em = pedidos.agora() - timedelta(days=31)
    with pytest.raises(RegraDeNegocio, match="30 dias"):
        devolucoes.registrar_devolucao(entregue.nova(), funcionario(), 8, devolucao(entregue))


def test_devolucao_nao_e_no_cd(entregue):
    with pytest.raises(RegraDeNegocio, match="loja ativa"):
        devolucoes.registrar_devolucao(entregue.nova(), funcionario(), 8, devolucao(entregue, loja=CD))


def test_categoria_do_chamado(entregue):
    entregue.chamado.categoria = "duvida"
    with pytest.raises(RegraDeNegocio, match="categoria troca"):
        devolucoes.registrar_devolucao(entregue.nova(), funcionario(), 8, devolucao(entregue))


def test_troca_por_outro_tamanho(entregue):
    com_estoque(entregue.banco, CALCA, LOJA_RJ, "loja_fisica", 3)
    db = entregue.nova()
    dados = {"id_unidade": LOJA_RJ, "itens": [{"id_variante": CAMISA, "quantidade": 1, "id_variante_nova": CALCA}]}

    pedido = devolucoes.registrar_troca(db, funcionario(), 8, dados)

    assert pedido["devolucao"] == "nenhuma" and pedido["valor_pago"] == Decimal("200.00")
    assert saldo(entregue.banco, CAMISA, LOJA_RJ, "loja_fisica") == (1, 0)
    assert saldo(entregue.banco, CALCA, LOJA_RJ, "loja_fisica") == (2, 0)
    # a peça recebida na troca pode ser devolvida depois
    assert devolucoes.em_maos(entregue.nova(), entregue.pedido) == {CAMISA: 1, CALCA: 1}


def test_troca_sem_estoque_da_peca_nova(entregue):
    com_estoque(entregue.banco, CALCA, LOJA_RJ, "loja_fisica", 0)
    dados = {"id_unidade": LOJA_RJ, "itens": [{"id_variante": CAMISA, "quantidade": 1, "id_variante_nova": CALCA}]}
    with pytest.raises(RegraDeNegocio, match="disponível insuficiente"):
        devolucoes.registrar_troca(entregue.nova(), funcionario(), 8, dados)


def test_estorno_avulso_parcial(entregue):
    pedido = devolucoes.registrar_estorno(entregue.nova(), 8, entregue.pix.id_pagamento, Decimal("30.00"))
    assert pedido["valor_pago"] == Decimal("170.00") and pedido["devolucao"] == "nenhuma"


def test_estorno_de_pagamento_de_outro_pedido(entregue):
    with pytest.raises(RecursoNaoEncontrado, match="não é deste pedido"):
        devolucoes.registrar_estorno(entregue.nova(), 8, 999, Decimal("1.00"))


def test_chamado_sem_pedido(entregue):
    entregue.chamado.id_pedido = None
    with pytest.raises(RegraDeNegocio, match="apontar para o pedido"):
        devolucoes.registrar_estorno(entregue.nova(), 8, 1, Decimal("1.00"))
