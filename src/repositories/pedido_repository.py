from datetime import datetime

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from src.models.avaliacoes import Avaliacao
from src.models.catalogo import Produto, Variante
from src.models.contas import Usuario
from src.models.estoque import Estoque, MovimentacaoEstoque, Unidade
from src.models.vendas import EnderecoEntrega, ItemPedido, Pagamento, Pedido


# travar=True segura a linha até o fim da transação: duas ações não mudam o mesmo pedido juntas
def buscar_pedido(db: Session, id_pedido: int, travar: bool = False) -> Pedido | None:
    consulta = select(Pedido).where(Pedido.id_pedido == id_pedido)
    if travar:
        consulta = consulta.with_for_update()
    return db.scalar(consulta.execution_options(populate_existing=True))


def pedido_por_codigo(db: Session, codigo_venda: str) -> Pedido | None:
    return db.scalar(select(Pedido).where(func.upper(Pedido.codigo_venda) == codigo_venda.upper()))


def buscar_pagamento(db: Session, id_pagamento: int, travar: bool = False) -> Pagamento | None:
    consulta = select(Pagamento).where(Pagamento.id_pagamento == id_pagamento)
    if travar:
        consulta = consulta.with_for_update()
    return db.scalar(consulta.execution_options(populate_existing=True))


# variante e produto de cada id pedido, para conferir se estão à venda e pegar o preço do momento
def variantes_com_produto(db: Session, ids_variante: list[int]) -> dict[int, tuple[Variante, Produto]]:
    consulta = select(Variante, Produto).join(Produto, Produto.id_produto == Variante.id_produto).where(
        Variante.id_variante.in_(ids_variante)
    )
    return {variante.id_variante: (variante, produto) for variante, produto in db.execute(consulta)}


# disponível (estoque − reservado) no canal online de cada unidade ativa, só das variantes pedidas
def disponivel_online(db: Session, ids_variante: list[int]) -> list:
    consulta = (
        select(
            Unidade.id_unidade, Unidade.nome, Unidade.tipo, Unidade.despacha_online, Unidade.cidade, Unidade.uf,
            Estoque.id_variante, (Estoque.quantidade - Estoque.quantidade_reservada).label("disponivel"),
        )
        .join(Estoque, Estoque.id_unidade == Unidade.id_unidade)
        .where(Unidade.ativo, Estoque.canal == "online", Estoque.id_variante.in_(ids_variante))
        .order_by(Unidade.id_unidade)
    )
    return list(db.execute(consulta).mappings().all())


# ---------- leitura dos pedidos ----------

def consulta_pedidos(
    id_cliente=None, status=None, canal=None, modalidade=None, id_unidade=None, codigo_venda=None,
    pronto_antes_de: datetime | None = None, cpf: str | None = None, entregue_desde: datetime | None = None,
):
    consulta = (
        select(Pedido, Usuario.nome.label("cliente"), Unidade.nome.label("unidade"))
        .join(Unidade, Unidade.id_unidade == Pedido.id_unidade)
        .outerjoin(Usuario, Usuario.id_usuario == Pedido.id_cliente)
        .order_by(Pedido.criado_em.desc(), Pedido.id_pedido.desc())
    )
    filtros = []
    for coluna, valor in [
        (Pedido.id_cliente, id_cliente), (Pedido.status, status), (Pedido.canal, canal),
        (Pedido.modalidade, modalidade), (Pedido.id_unidade, id_unidade),
    ]:
        if valor is not None:
            filtros.append(coluna == valor)
    if codigo_venda:
        filtros.append(func.upper(Pedido.codigo_venda) == codigo_venda.upper())
    # CPF da conta ou CPF na nota (balcão de troca e devolução)
    if cpf is not None:
        filtros.append(or_(Pedido.cpf_nota == cpf, Usuario.cpf == cpf))
    if entregue_desde is not None:
        filtros.append(Pedido.entregue_em >= entregue_desde)
    if pronto_antes_de is not None:
        filtros.append(Pedido.status == "pronto_para_retirada")
        filtros.append(Pedido.pronto_retirada_em < pronto_antes_de)
    return consulta.where(*filtros)


def listar_pedidos(db: Session, limit: int, offset: int, **filtros) -> tuple[list, int]:
    consulta = consulta_pedidos(**filtros)
    total = db.scalar(select(func.count()).select_from(consulta.order_by(None).subquery()))
    return list(db.execute(consulta.limit(limit).offset(offset)).all()), total


def pedido_com_nomes(db: Session, id_pedido: int):
    return db.execute(consulta_pedidos().where(Pedido.id_pedido == id_pedido)).first()


def itens_dos_pedidos(db: Session, ids_pedido: list[int]) -> dict[int, list]:
    agrupados: dict[int, list] = {i: [] for i in ids_pedido}
    if ids_pedido:
        consulta = (
            select(
                ItemPedido.id_item, ItemPedido.id_pedido, ItemPedido.id_variante, Variante.sku,
                Produto.nome.label("produto"), Variante.cor, Variante.tamanho, ItemPedido.quantidade,
                ItemPedido.preco_unitario, Avaliacao.id_avaliacao,
            )
            .join(Variante, Variante.id_variante == ItemPedido.id_variante)
            .join(Produto, Produto.id_produto == Variante.id_produto)
            .outerjoin(Avaliacao, Avaliacao.id_item_pedido == ItemPedido.id_item)
            .where(ItemPedido.id_pedido.in_(ids_pedido))
            .order_by(ItemPedido.id_item)
        )
        for linha in db.execute(consulta).mappings():
            agrupados[linha["id_pedido"]].append(linha)
    return agrupados


def pagamentos_dos_pedidos(db: Session, ids_pedido: list[int]) -> dict[int, list[Pagamento]]:
    agrupados: dict[int, list[Pagamento]] = {i: [] for i in ids_pedido}
    if ids_pedido:
        consulta = (
            select(Pagamento).where(Pagamento.id_pedido.in_(ids_pedido))
            .order_by(Pagamento.criado_em, Pagamento.id_pagamento)
        )
        for pagamento in db.scalars(consulta):
            agrupados[pagamento.id_pedido].append(pagamento)
    return agrupados


def enderecos_dos_pedidos(db: Session, ids_pedido: list[int]) -> dict[int, EnderecoEntrega]:
    if not ids_pedido:
        return {}
    consulta = select(EnderecoEntrega).where(EnderecoEntrega.id_pedido.in_(ids_pedido))
    return {endereco.id_pedido: endereco for endereco in db.scalars(consulta)}


# itens crus (sem nomes) de um pedido, para as regras de estoque e devolução
def itens_do_pedido(db: Session, id_pedido: int) -> list[ItemPedido]:
    consulta = select(ItemPedido).where(ItemPedido.id_pedido == id_pedido).order_by(ItemPedido.id_item)
    return list(db.scalars(consulta))


# peças que já voltaram (devolucao) ou saíram em troca (saida_troca) no pedido, pelo Atendimento ou
# no balcão: toda troca e devolução guarda o pedido (ADR 0015)
def trocas_e_devolucoes(db: Session, id_pedido: int) -> list:
    m = MovimentacaoEstoque
    consulta = (
        select(m.tipo, m.id_variante, func.sum(m.quantidade))
        .where(m.id_pedido == id_pedido, m.tipo.in_(("devolucao", "saida_troca")))
        .group_by(m.tipo, m.id_variante)
    )
    return list(db.execute(consulta).all())


# compras da loja com o CPF na nota e ainda sem conta passam para a conta desse CPF (ADR 0014);
# devolve quantas foram ligadas
def ligar_pedidos_pelo_cpf(db: Session, cpf: str, id_cliente) -> int:
    resultado = db.execute(
        update(Pedido).where(Pedido.cpf_nota == cpf, Pedido.id_cliente.is_(None)).values(id_cliente=id_cliente)
    )
    return resultado.rowcount


# correção de CPF que já tinha conta: os pedidos passam para a conta certa (case, seção 5)
def transferir_pedidos(db: Session, de, para) -> None:
    db.execute(update(Pedido).where(Pedido.id_cliente == de).values(id_cliente=para))
