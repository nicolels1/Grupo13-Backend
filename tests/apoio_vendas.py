import itertools
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.models.catalogo import Produto, Variante
from src.models.vendas import EnderecoCliente, EnderecoEntrega, ItemPedido, Pagamento, Pedido
from src.repositories import endereco_repository, estoque_repository, unidade_repository, usuario_repository
from src.repositories import pedido_repository
from tests.apoio import SessaoComTrigger, funcionario

ID_CLIENTE = uuid.UUID("44444444-4444-4444-4444-444444444444")
ID_OUTRO = uuid.UUID("55555555-5555-5555-5555-555555555555")
LOJA_SP, LOJA_RJ, CD, LOJA_FECHADA = 1, 2, 3, 4
CAMISA, CALCA, INATIVA = 10, 11, 12


def cliente(id_usuario=ID_CLIENTE):
    return funcionario(id_usuario=id_usuario, tipo_conta="cliente", id_modelo_acesso=None, nome="Marina")


def _unidade(id_unidade, nome, tipo, despacha, cidade, uf, ativo=True):
    return SimpleNamespace(id_unidade=id_unidade, nome=nome, tipo=tipo, despacha_online=despacha, cidade=cidade,
                           uf=uf, ativo=ativo)


class SessaoVendas(SessaoComTrigger):
    """Guarda pedidos, itens, pagamentos e endereços como o banco guardaria. As sessões do mesmo
    banco usam um contador de ids só, para dois pedidos nunca terem o mesmo número."""

    def __init__(self, banco):
        super().__init__(banco.saldos)
        self.banco = banco
        self._ids = banco.ids

    def flush(self):
        super().flush()
        for objeto in self.adicionados:
            if isinstance(objeto, Pedido):
                self.banco.pedidos[objeto.id_pedido] = objeto
            elif isinstance(objeto, ItemPedido) and objeto not in self.banco.itens:
                self.banco.itens.append(objeto)
            elif isinstance(objeto, Pagamento) and objeto not in self.banco.pagamentos:
                self.banco.pagamentos.append(objeto)
            elif isinstance(objeto, EnderecoEntrega):
                self.banco.enderecos_entrega[objeto.id_pedido] = objeto

    def commit(self):
        self.flush()
        super().commit()


@pytest.fixture
def banco(monkeypatch):
    produto = Produto(id_produto=1, id_categoria=1, nome="Camisa", descricao_tecnica="t", descricao_cliente="c",
                      ativo=True)
    estado = SimpleNamespace(
        variantes={
            CAMISA: (Variante(id_variante=CAMISA, id_produto=1, sku="CAM-M", cor="Azul", tamanho="M",
                              preco=Decimal("100.00"), ativo=True), produto),
            CALCA: (Variante(id_variante=CALCA, id_produto=1, sku="CAM-G", cor="Azul", tamanho="G",
                             preco=Decimal("250.00"), ativo=True), produto),
            INATIVA: (Variante(id_variante=INATIVA, id_produto=1, sku="CAM-P", cor="Azul", tamanho="P",
                               preco=Decimal("100.00"), ativo=False), produto),
        },
        unidades={
            LOJA_SP: _unidade(LOJA_SP, "Loja Paulista", "loja", False, "São Paulo", "SP"),
            LOJA_RJ: _unidade(LOJA_RJ, "Loja Rio", "loja", True, "Rio de Janeiro", "RJ"),
            CD: _unidade(CD, "CD Jundiaí", "cd", True, "Jundiaí", "SP"),
            LOJA_FECHADA: _unidade(LOJA_FECHADA, "Loja Fechada", "loja", True, "São Paulo", "SP", ativo=False),
        },
        saldos={},
        pedidos={},
        itens=[],
        pagamentos=[],
        enderecos_entrega={},
        enderecos={7: EnderecoCliente(id_endereco=7, id_cliente=ID_CLIENTE, rua="Rua A", numero="1", complemento=None,
                                      bairro="Centro", cidade="Sao Paulo", uf="SP", cep="01000000")},
        clientes_por_cpf={},
        ids=itertools.count(1),
    )

    def disponivel_online(db, ids):
        linhas = []
        for (id_variante, id_unidade, canal), linha in estado.saldos.items():
            unidade = estado.unidades[id_unidade]
            if canal == "online" and id_variante in ids and unidade.ativo:
                linhas.append({"id_unidade": id_unidade, "nome": unidade.nome, "tipo": unidade.tipo,
                               "despacha_online": unidade.despacha_online, "cidade": unidade.cidade, "uf": unidade.uf,
                               "id_variante": id_variante, "disponivel": linha.quantidade - linha.quantidade_reservada})
        return sorted(linhas, key=lambda l: l["id_unidade"])

    def pedido_com_nomes(db, id_pedido):
        pedido = estado.pedidos.get(id_pedido)
        return None if pedido is None else (pedido, "Marina", estado.unidades[pedido.id_unidade].nome)

    m = monkeypatch.setattr
    r = pedido_repository
    m(r, "variantes_com_produto", lambda db, ids: {i: estado.variantes[i] for i in ids if i in estado.variantes})
    m(r, "disponivel_online", disponivel_online)
    m(r, "buscar_pedido", lambda db, i, travar=False: estado.pedidos.get(i))
    m(r, "pedido_por_codigo", lambda db, c: next(
        (p for p in estado.pedidos.values() if p.codigo_venda.upper() == c.upper()), None))
    m(r, "buscar_pagamento", lambda db, i, travar=False: next(
        (p for p in estado.pagamentos if p.id_pagamento == i), None))
    m(r, "pedido_com_nomes", pedido_com_nomes)
    m(r, "listar_pedidos", lambda db, limit, offset, **f: (
        [pedido_com_nomes(db, i) for i in estado.pedidos], len(estado.pedidos)))
    m(r, "itens_dos_pedidos", lambda db, ids: {i: [x for x in estado.itens if x.id_pedido == i] for i in ids})
    m(r, "pagamentos_dos_pedidos", lambda db, ids: {
        i: [p for p in estado.pagamentos if p.id_pedido == i] for i in ids})
    m(r, "enderecos_dos_pedidos", lambda db, ids: {i: estado.enderecos_entrega[i] for i in ids
                                                   if i in estado.enderecos_entrega})
    m(r, "itens_do_pedido", lambda db, i: [x for x in estado.itens if x.id_pedido == i])
    m(estoque_repository, "travar_estoque", lambda db, v, u, canais: {
        c: estado.saldos[(v, u, c)] for c in canais if (v, u, c) in estado.saldos})
    m(unidade_repository, "buscar_unidade", lambda db, u: estado.unidades.get(u))
    m(endereco_repository, "buscar_endereco", lambda db, i: estado.enderecos.get(i))
    m(usuario_repository, "buscar_cliente_por_cpf", lambda db, cpf: estado.clientes_por_cpf.get(cpf))
    return estado


def com_estoque(banco, variante, unidade, canal, quantidade, reservada=0):
    banco.saldos[(variante, unidade, canal)] = SimpleNamespace(quantidade=quantidade, quantidade_reservada=reservada)


def saldo(banco, variante, unidade, canal="online"):
    linha = banco.saldos[(variante, unidade, canal)]
    return linha.quantidade, linha.quantidade_reservada
