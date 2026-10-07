from datetime import datetime

from sqlalchemy import distinct, func, or_, select
from sqlalchemy.orm import Session

from src.models.atendimento import Chamado, Mensagem
from src.models.avaliacoes import Avaliacao, DenunciaAvaliacao
from src.models.catalogo import Produto, Variante
from src.models.estoque import Estoque, Transferencia, Unidade
from src.models.vendas import ItemPedido, Pedido

FUSO = "America/Sao_Paulo"


def _dia(coluna):
    """Data no horário de Brasília (o dia em que aconteceu para quem está na loja)."""
    return func.date(func.timezone(FUSO, coluna))


# venda conta no dia em que foi paga; cancelado fica de fora (inclusive o pago e depois cancelado)
def _vendas(desde: datetime, ate: datetime | None = None, id_unidade: int | None = None) -> list:
    filtros = [Pedido.pago_em.is_not(None), Pedido.pago_em >= desde, Pedido.status != "cancelado"]
    if ate is not None:
        filtros.append(Pedido.pago_em < ate)
    if id_unidade is not None:
        filtros.append(Pedido.id_unidade == id_unidade)
    return filtros


# ---------- vendas ----------

def vendas_por_dia(db: Session, desde: datetime, id_unidade: int | None) -> list:
    """(dia, canal, pedidos, valor) desde a data, por dia e canal."""
    dia = _dia(Pedido.pago_em)
    consulta = (
        select(dia.label("dia"), Pedido.canal, func.count().label("pedidos"),
               func.sum(Pedido.valor_total).label("valor"))
        .where(*_vendas(desde, id_unidade=id_unidade))
        .group_by(dia, Pedido.canal)
    )
    return list(db.execute(consulta).mappings().all())


def vendas_por_canal(db: Session, desde: datetime, ate: datetime | None = None) -> list:
    """(canal, pedidos, valor) no período, na rede inteira."""
    consulta = (
        select(Pedido.canal, func.count().label("pedidos"), func.sum(Pedido.valor_total).label("valor"))
        .where(*_vendas(desde, ate))
        .group_by(Pedido.canal)
    )
    return list(db.execute(consulta).mappings().all())


def mais_vendidas(db: Session, desde: datetime, id_unidade: int | None, limite: int) -> list:
    """Variantes com mais peças vendidas no período, com produto, cor e tamanho."""
    vendidas = func.sum(ItemPedido.quantidade)
    consulta = (
        select(
            ItemPedido.id_variante, Variante.id_produto, Produto.nome.label("produto"), Variante.cor,
            Variante.tamanho, Variante.sku, vendidas.label("quantidade_vendida"),
        )
        .join(Pedido, Pedido.id_pedido == ItemPedido.id_pedido)
        .join(Variante, Variante.id_variante == ItemPedido.id_variante)
        .join(Produto, Produto.id_produto == Variante.id_produto)
        .where(*_vendas(desde, id_unidade=id_unidade))
        .group_by(ItemPedido.id_variante, Variante.id_produto, Produto.nome, Variante.cor, Variante.tamanho,
                  Variante.sku)
        .order_by(vendidas.desc(), ItemPedido.id_variante)
        .limit(limite)
    )
    return list(db.execute(consulta).mappings().all())


def saldos_das_variantes(db: Session, ids_variante: list[int], id_unidade: int | None) -> dict[int, int]:
    """Saldo atual (loja física + online) de cada variante, na unidade ou na rede."""
    if not ids_variante:
        return {}
    consulta = select(Estoque.id_variante, func.sum(Estoque.quantidade)).where(Estoque.id_variante.in_(ids_variante))
    if id_unidade is not None:
        consulta = consulta.where(Estoque.id_unidade == id_unidade)
    return {id_variante: int(saldo) for id_variante, saldo in db.execute(consulta.group_by(Estoque.id_variante))}


# ---------- chamados ----------

def chamados_por_status(db: Session, concluidos_desde: datetime, id_unidade: int | None) -> dict[str, int]:
    """Abertos e em andamento agora; concluídos só desde a data."""
    filtros = [or_(Chamado.status != "concluido", Chamado.concluido_em >= concluidos_desde)]
    if id_unidade is not None:
        filtros.append(Chamado.id_unidade == id_unidade)
    consulta = select(Chamado.status, func.count()).where(*filtros).group_by(Chamado.status)
    return dict(db.execute(consulta).all())


def chamados_por_dia(db: Session, desde: datetime, id_unidade: int | None) -> tuple[dict, dict]:
    """({dia: abertos}, {dia: concluídos}) desde a data."""
    resultado = []
    for coluna in (Chamado.criado_em, Chamado.concluido_em):
        dia = _dia(coluna)
        consulta = select(dia, func.count()).where(coluna >= desde)
        if id_unidade is not None:
            consulta = consulta.where(Chamado.id_unidade == id_unidade)
        resultado.append(dict(db.execute(consulta.group_by(dia)).all()))
    return resultado[0], resultado[1]


def primeira_resposta(db: Session, desde: datetime) -> tuple[float | None, int]:
    """(mediana em horas até a primeira mensagem da equipe visível ao cliente, chamados ainda sem ela)
    dos chamados abertos desde a data."""
    primeira = (
        select(func.min(Mensagem.criado_em))
        .where(Mensagem.id_chamado == Chamado.id_chamado, Mensagem.id_autor != Chamado.id_cliente,
               Mensagem.interna.is_(False))
        .correlate(Chamado)
        .scalar_subquery()
    )
    horas = func.extract("epoch", primeira - Chamado.criado_em) / 3600
    mediana = db.scalar(
        select(func.percentile_cont(0.5).within_group(horas)).where(Chamado.criado_em >= desde, primeira.is_not(None))
    )
    sem_resposta = db.scalar(
        select(func.count()).select_from(Chamado)
        .where(Chamado.criado_em >= desde, Chamado.status != "concluido", primeira.is_(None))
    )
    return (float(mediana) if mediana is not None else None), sem_resposta


# ---------- estoque e rede ----------

def saldo_total(db: Session, id_unidade: int | None = None) -> int:
    consulta = select(func.coalesce(func.sum(Estoque.quantidade), 0))
    if id_unidade is not None:
        consulta = consulta.where(Estoque.id_unidade == id_unidade)
    return int(db.scalar(consulta))


def pecas_vendidas_por_unidade(db: Session, desde: datetime) -> dict[int, int]:
    consulta = (
        select(Pedido.id_unidade, func.sum(ItemPedido.quantidade))
        .join(Pedido, Pedido.id_pedido == ItemPedido.id_pedido)
        .where(*_vendas(desde))
        .group_by(Pedido.id_unidade)
    )
    return {id_unidade: int(soma) for id_unidade, soma in db.execute(consulta)}


def ruptura_online(db: Session) -> tuple[int, int]:
    """(variantes à venda, variantes sem peça disponível online em CD ou loja que despacha)."""
    disponivel_online = (
        select(Estoque.id_variante)
        .join(Unidade, Unidade.id_unidade == Estoque.id_unidade)
        .where(Estoque.canal == "online", Unidade.ativo, Unidade.despacha_online,
               Estoque.quantidade - Estoque.quantidade_reservada > 0)
    )
    a_venda = (
        select(Variante.id_variante).join(Produto, Produto.id_produto == Variante.id_produto)
        .where(Variante.ativo, Produto.ativo)
    )
    total = db.scalar(select(func.count()).select_from(a_venda.subquery()))
    sem = db.scalar(select(func.count()).select_from(
        a_venda.where(Variante.id_variante.not_in(disponivel_online)).subquery()))
    return total, sem


def avaliacoes(db: Session, desde: datetime) -> tuple[float | None, int, int]:
    """(nota média e quantidade das publicadas desde a data, denúncias pendentes)."""
    media, quantidade = db.execute(
        select(func.avg(Avaliacao.nota), func.count())
        .where(Avaliacao.status == "publicada", Avaliacao.criada_em >= desde)
    ).one()
    pendentes = db.scalar(select(func.count()).select_from(DenunciaAvaliacao)
                          .where(DenunciaAvaliacao.status == "pendente"))
    return (float(media) if media is not None else None), quantidade, pendentes


def retiradas_perto_de_vencer(db: Session, prontas_antes_de: datetime) -> dict[int, int]:
    """Retiradas prontas antes da data, por unidade."""
    consulta = (
        select(Pedido.id_unidade, func.count())
        .where(Pedido.status == "pronto_para_retirada", Pedido.pronto_retirada_em < prontas_antes_de)
        .group_by(Pedido.id_unidade)
    )
    return dict(db.execute(consulta).all())


# ---------- por unidade ----------

def unidades_ativas(db: Session) -> list[Unidade]:
    return list(db.scalars(select(Unidade).where(Unidade.ativo).order_by(Unidade.tipo.desc(), Unidade.nome)))


def vendas_por_unidade(db: Session, desde: datetime) -> dict[int, tuple[int, object]]:
    consulta = (
        select(Pedido.id_unidade, func.count(), func.sum(Pedido.valor_total))
        .where(*_vendas(desde))
        .group_by(Pedido.id_unidade)
    )
    return {id_unidade: (pedidos, valor) for id_unidade, pedidos, valor in db.execute(consulta)}


def saldo_por_unidade(db: Session) -> dict[int, int]:
    consulta = select(Estoque.id_unidade, func.sum(Estoque.quantidade)).group_by(Estoque.id_unidade)
    return {id_unidade: int(saldo) for id_unidade, saldo in db.execute(consulta)}


# mesma regra do filtro abaixo_minimo do estoque: mínimo definido e saldo menor que ele
def abaixo_do_minimo_por_unidade(db: Session) -> dict[int, int]:
    consulta = (
        select(Estoque.id_unidade, func.count(distinct(Estoque.id_variante)))
        .where(Estoque.estoque_minimo.is_not(None), Estoque.quantidade < Estoque.estoque_minimo)
        .group_by(Estoque.id_unidade)
    )
    return dict(db.execute(consulta).all())


def transferencias_por_unidade(db: Session) -> tuple[dict[int, int], dict[int, int]]:
    """({origem: esperando envio}, {destino: chegando})."""
    esperando = select(Transferencia.id_unidade_origem, func.count()).where(Transferencia.status == "solicitada")
    chegando = select(Transferencia.id_unidade_destino, func.count()).where(Transferencia.status == "enviada")
    return (dict(db.execute(esperando.group_by(Transferencia.id_unidade_origem)).all()),
            dict(db.execute(chegando.group_by(Transferencia.id_unidade_destino)).all()))
