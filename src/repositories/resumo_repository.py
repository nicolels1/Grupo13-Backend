from datetime import datetime

from sqlalchemy import String, and_, case, distinct, func, literal, or_, select, union_all
from sqlalchemy.orm import Session, aliased

from src.models.atendimento import Chamado, Mensagem
from src.models.avaliacoes import Avaliacao, DenunciaAvaliacao
from src.models.catalogo import HistoricoPreco, Produto, Variante
from src.models.contas import Usuario
from src.models.estoque import Estoque, Transferencia, Unidade
from src.models.vendas import ItemPedido, Pagamento, Pedido
from src.repositories import atendimento_repository

FUSO = "America/Sao_Paulo"


def _dia(coluna):
    """Data no horário de Brasília (o dia em que aconteceu para quem está na loja)."""
    return func.date(func.timezone(FUSO, coluna))


# a marca da venda, do chamado e do estoque é a da unidade (a mesma do resgate de pontos)
def _da_marca(coluna, marca: str):
    return coluna.in_(select(Unidade.id_unidade).where(Unidade.marca == marca))


# período [desde, ate) de uma coluna de data
def _entre(coluna, desde: datetime, ate: datetime | None) -> list:
    return [coluna >= desde] if ate is None else [coluna >= desde, coluna < ate]


# venda conta no dia em que foi paga; cancelado fica de fora (inclusive o pago e depois cancelado)
def _vendas(desde: datetime, ate: datetime | None = None, id_unidade: int | None = None,
            marca: str | None = None) -> list:
    filtros = [Pedido.pago_em.is_not(None), *_entre(Pedido.pago_em, desde, ate), Pedido.status != "cancelado"]
    if id_unidade is not None:
        filtros.append(Pedido.id_unidade == id_unidade)
    if marca is not None:
        filtros.append(_da_marca(Pedido.id_unidade, marca))
    return filtros


# Faturamento, com a mesma regra dos três números da Vulto: o pedido pago entra no dia do pagamento sem o
# frete, que é repasse para a transportadora (V7), e o estorno sai no dia em que foi feito (V4, V6, ADR 0003).
# O pedido estornado inteiro devolveu o frete junto e dá zero. O cancelado fica de fora com os estornos dele.
# Cada lançamento leva o canal da venda (loja, site ou app, P3) e a marca da unidade
def _lancamentos():
    estorno = and_(Pagamento.tipo == "estorno", Pagamento.status == "aprovado")
    devolvido = (select(func.coalesce(func.sum(Pagamento.valor), 0))
                 .where(Pagamento.id_pedido == Pedido.id_pedido, estorno).scalar_subquery())
    frete = case((devolvido >= Pedido.valor_total, 0), else_=Pedido.valor_frete)
    vendas = (select(Pedido.pago_em.label("quando"), Pedido.canal_venda, Pedido.id_unidade, Unidade.marca,
                     literal(1).label("pedidos"), (Pedido.valor_total - frete).label("valor"), frete.label("frete"))
              .join(Unidade, Unidade.id_unidade == Pedido.id_unidade)
              .where(Pedido.pago_em.is_not(None), Pedido.status != "cancelado"))
    estornos = (select(Pagamento.criado_em, Pedido.canal_venda, Pedido.id_unidade, Unidade.marca, literal(0),
                       -Pagamento.valor, literal(0))
                .join(Pedido, Pedido.id_pedido == Pagamento.id_pedido)
                .join(Unidade, Unidade.id_unidade == Pedido.id_unidade)
                .where(estorno, Pedido.status != "cancelado"))
    return union_all(vendas, estornos).subquery("lancamento")


def _no_periodo(lancamento, desde: datetime, ate: datetime | None = None, id_unidade: int | None = None,
                marca: str | None = None) -> list:
    filtros = _entre(lancamento.c.quando, desde, ate)
    if id_unidade is not None:
        filtros.append(lancamento.c.id_unidade == id_unidade)
    if marca is not None:
        filtros.append(lancamento.c.marca == marca)
    return filtros


# ---------- vendas ----------

def vendas_por_dia(db: Session, desde: datetime, ate: datetime, id_unidade: int | None, marca: str | None) -> list:
    """(dia, canal_venda, pedidos, valor) no período, por dia e canal da venda."""
    lancamento = _lancamentos()
    dia = _dia(lancamento.c.quando)
    consulta = (
        select(dia.label("dia"), lancamento.c.canal_venda, func.sum(lancamento.c.pedidos).label("pedidos"),
               func.sum(lancamento.c.valor).label("valor"))
        .where(*_no_periodo(lancamento, desde, ate, id_unidade, marca))
        .group_by(dia, lancamento.c.canal_venda)
    )
    return list(db.execute(consulta).mappings().all())


def vendas_por_canal(db: Session, desde: datetime, ate: datetime | None = None, marca: str | None = None) -> list:
    """(canal_venda, pedidos, valor) no período, na rede inteira ou na marca."""
    lancamento = _lancamentos()
    consulta = (
        select(lancamento.c.canal_venda, func.sum(lancamento.c.pedidos).label("pedidos"),
               func.sum(lancamento.c.valor).label("valor"))
        .where(*_no_periodo(lancamento, desde, ate, marca=marca))
        .group_by(lancamento.c.canal_venda)
    )
    return list(db.execute(consulta).mappings().all())


def vendas_por_marca(db: Session, desde: datetime, ate: datetime | None = None, marca: str | None = None) -> list:
    """(marca, pedidos, valor) no período."""
    lancamento = _lancamentos()
    consulta = (
        select(lancamento.c.marca, func.sum(lancamento.c.pedidos).label("pedidos"),
               func.sum(lancamento.c.valor).label("valor"))
        .where(*_no_periodo(lancamento, desde, ate, marca=marca))
        .group_by(lancamento.c.marca)
    )
    return list(db.execute(consulta).mappings().all())


# faturamento por mês (horário de Brasília), unidade e canal da venda: a mesma conta dos três números da Vulto
def faturamento_por_mes(db: Session, desde: datetime, ate: datetime, id_unidade: int | None,
                        marca: str | None) -> list:
    """(mes, id_unidade, unidade, canal_venda, pedidos, valor, frete) no período."""
    lancamento = _lancamentos()
    mes = func.date(func.date_trunc("month", func.timezone(FUSO, lancamento.c.quando)))
    consulta = (
        select(mes.label("mes"), lancamento.c.id_unidade, Unidade.nome.label("unidade"), lancamento.c.canal_venda,
               func.sum(lancamento.c.pedidos).label("pedidos"), func.sum(lancamento.c.valor).label("valor"),
               func.sum(lancamento.c.frete).label("frete"))
        .join(Unidade, Unidade.id_unidade == lancamento.c.id_unidade)
        .where(*_no_periodo(lancamento, desde, ate, id_unidade, marca))
        .group_by(mes, lancamento.c.id_unidade, Unidade.nome, lancamento.c.canal_venda)
        .order_by(mes, Unidade.nome, lancamento.c.canal_venda)
    )
    return list(db.execute(consulta).mappings().all())


def mais_vendidas(db: Session, desde: datetime, ate: datetime, id_unidade: int | None, marca: str | None,
                  limite: int) -> list:
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
        .where(*_vendas(desde, ate, id_unidade, marca))
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

# a marca do chamado segue a mesma regra da fila do Atendimento
def _chamados_da(id_unidade: int | None, marca: str | None) -> list:
    filtros = []
    if id_unidade is not None:
        filtros.append(Chamado.id_unidade == id_unidade)
    if marca is not None:
        filtros.append(atendimento_repository.da_marca(marca))
    return filtros


def chamados_por_status(db: Session, concluidos_desde: datetime, concluidos_ate: datetime, id_unidade: int | None,
                        marca: str | None) -> dict[str, int]:
    """Abertos e em andamento agora; concluídos só no período."""
    concluido_no_periodo = and_(*_entre(Chamado.concluido_em, concluidos_desde, concluidos_ate))
    filtros = [or_(Chamado.status != "concluido", concluido_no_periodo), *_chamados_da(id_unidade, marca)]
    consulta = select(Chamado.status, func.count()).where(*filtros).group_by(Chamado.status)
    return dict(db.execute(consulta).all())


def chamados_por_dia(db: Session, desde: datetime, ate: datetime, id_unidade: int | None,
                     marca: str | None) -> tuple[dict, dict]:
    """({dia: abertos}, {dia: concluídos}) no período."""
    resultado = []
    for coluna in (Chamado.criado_em, Chamado.concluido_em):
        dia = _dia(coluna)
        consulta = select(dia, func.count()).where(*_entre(coluna, desde, ate), *_chamados_da(id_unidade, marca))
        resultado.append(dict(db.execute(consulta.group_by(dia)).all()))
    return resultado[0], resultado[1]


def primeira_resposta(db: Session, desde: datetime, ate: datetime | None = None,
                      marca: str | None = None) -> tuple[float | None, int]:
    """(mediana em horas até a primeira mensagem da equipe visível ao cliente, chamados ainda sem ela)
    dos chamados abertos no período."""
    primeira = (
        select(func.min(Mensagem.criado_em))
        # fala da equipe: com autor e não do cliente (a fala sem autor é do cliente no atendimento migrado, P6)
        .where(Mensagem.id_chamado == Chamado.id_chamado, Mensagem.id_autor.is_not(None),
               Mensagem.id_autor.is_distinct_from(Chamado.id_cliente), Mensagem.interna.is_(False))
        .correlate(Chamado)
        .scalar_subquery()
    )
    abertos = [*_entre(Chamado.criado_em, desde, ate), *_chamados_da(None, marca)]
    horas = func.extract("epoch", primeira - Chamado.criado_em) / 3600
    mediana = db.scalar(
        select(func.percentile_cont(0.5).within_group(horas)).where(*abertos, primeira.is_not(None))
    )
    sem_resposta = db.scalar(
        select(func.count()).select_from(Chamado)
        .where(*abertos, Chamado.status != "concluido", primeira.is_(None))
    )
    return (float(mediana) if mediana is not None else None), sem_resposta


# ---------- estoque e rede ----------

def saldo_total(db: Session, marca: str | None = None) -> int:
    consulta = select(func.coalesce(func.sum(Estoque.quantidade), 0))
    if marca is not None:
        consulta = consulta.where(_da_marca(Estoque.id_unidade, marca))
    return int(db.scalar(consulta))


def pecas_vendidas_por_unidade(db: Session, desde: datetime, ate: datetime | None = None,
                               marca: str | None = None) -> dict[int, int]:
    consulta = (
        select(Pedido.id_unidade, func.sum(ItemPedido.quantidade))
        .join(Pedido, Pedido.id_pedido == ItemPedido.id_pedido)
        .where(*_vendas(desde, ate, marca=marca))
        .group_by(Pedido.id_unidade)
    )
    return {id_unidade: int(soma) for id_unidade, soma in db.execute(consulta)}


def ruptura_online(db: Session, marca: str | None = None) -> tuple[int, int]:
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
    if marca is not None:
        a_venda = a_venda.where(Produto.marca == marca)
    total = db.scalar(select(func.count()).select_from(a_venda.subquery()))
    sem = db.scalar(select(func.count()).select_from(
        a_venda.where(Variante.id_variante.not_in(disponivel_online)).subquery()))
    return total, sem


def avaliacoes(db: Session, desde: datetime, ate: datetime | None = None,
               marca: str | None = None) -> tuple[float | None, int, int]:
    """(nota média e quantidade das publicadas no período, denúncias pendentes), da marca do produto."""
    da_marca = []
    if marca is not None:
        da_marca.append(Avaliacao.id_item_pedido.in_(
            select(ItemPedido.id_item)
            .join(Variante, Variante.id_variante == ItemPedido.id_variante)
            .join(Produto, Produto.id_produto == Variante.id_produto)
            .where(Produto.marca == marca)
        ))
    media, quantidade = db.execute(
        select(func.avg(Avaliacao.nota), func.count())
        .where(Avaliacao.status == "publicada", *_entre(Avaliacao.criada_em, desde, ate), *da_marca)
    ).one()
    pendentes = db.scalar(select(func.count()).select_from(DenunciaAvaliacao)
                          .join(Avaliacao, Avaliacao.id_avaliacao == DenunciaAvaliacao.id_avaliacao)
                          .where(DenunciaAvaliacao.status == "pendente", *da_marca))
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

def unidades_ativas(db: Session, marca: str | None = None) -> list[Unidade]:
    consulta = select(Unidade).where(Unidade.ativo)
    if marca is not None:
        consulta = consulta.where(Unidade.marca == marca)
    return list(db.scalars(consulta.order_by(Unidade.tipo.desc(), Unidade.nome)))


def vendas_por_unidade(db: Session, desde: datetime, ate: datetime | None = None) -> dict[int, tuple[int, object]]:
    lancamento = _lancamentos()
    consulta = (
        select(lancamento.c.id_unidade, func.sum(lancamento.c.pedidos), func.sum(lancamento.c.valor))
        .where(*_no_periodo(lancamento, desde, ate))
        .group_by(lancamento.c.id_unidade)
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


# ---------- desempenho da equipe (assistente interno, só Admin) ----------

# vendas da loja física no período por quem registrou (a venda online não tem vendedor): pedido pago no período,
# sem cancelados, sem o frete. Devoluções não são descontadas aqui
def vendas_por_vendedor(db: Session, desde: datetime, ate: datetime, id_unidade: int | None,
                        marca: str | None) -> list:
    valor = Pedido.valor_total - Pedido.valor_frete
    consulta = (
        select(Usuario.nome.label("vendedor"), func.count().label("pedidos"), func.sum(valor).label("valor"),
               func.count(distinct(Pedido.id_unidade)).label("unidades"))
        .join(Usuario, Usuario.id_usuario == Pedido.id_registrado_por)
        .where(*_vendas(desde, ate, id_unidade, marca), Pedido.canal_venda == "loja")
        .group_by(Usuario.id_usuario, Usuario.nome)
        .order_by(func.sum(valor).desc(), Usuario.nome)
    )
    return list(db.execute(consulta).mappings().all())


# chamados concluídos no período por responsável, com a mediana de horas da abertura até a conclusão. Os migrados
# da Vulto não guardam quando foram concluídos (concluido_em = criado_em): ficam fora da mediana, que sem
# nenhum chamado com tempo de verdade vem vazia
def chamados_por_atendente(db: Session, desde: datetime, ate: datetime, id_unidade: int | None,
                           marca: str | None) -> list:
    horas = case((Chamado.concluido_em > Chamado.criado_em,
                  func.extract("epoch", Chamado.concluido_em - Chamado.criado_em) / 3600))
    consulta = (
        select(Usuario.nome.label("atendente"), func.count().label("concluidos"),
               func.percentile_cont(0.5).within_group(horas).label("mediana_horas"))
        .join(Usuario, Usuario.id_usuario == Chamado.id_responsavel)
        .where(Chamado.status == "concluido", *_entre(Chamado.concluido_em, desde, ate),
               *_chamados_da(id_unidade, marca))
        .group_by(Usuario.id_usuario, Usuario.nome)
        .order_by(func.count().desc(), Usuario.nome)
    )
    return list(db.execute(consulta).mappings().all())


# ---------- anomalias (assistente interno, só Admin) ----------

EXEMPLOS_DE_ANOMALIA = 5
PRECO_FORA_DO_CATALOGO = 0.5  # mais de 50% acima ou abaixo do preço da época
VEZES_A_MEDIANA = 5
PEDIDOS_PARA_MEDIANA = 20  # com menos pedidos no canal, a mediana não diz o que é normal
REPETIDO_EM = 600  # segundos entre dois pedidos iguais


# item vendido longe do preço que valia na data da venda (o do histórico de preços; sem histórico, o de hoje).
# Sem preço de referência (ex.: a peça "sem cadastro" da migração) não dá para comparar
def precos_fora_do_catalogo(db: Session, desde: datetime, ate: datetime, id_unidade: int | None,
                            marca: str | None) -> list:
    # cada preço do histórico vale da alteração até a próxima: uma faixa por preço, cruzada de uma vez com as vendas
    # (buscar o preço da época item a item levava segundos num período longo)
    h = HistoricoPreco
    faixas = select(
        h.id_variante, h.preco_novo.label("preco"), h.alterado_em.label("desde"),
        func.lead(h.alterado_em).over(partition_by=h.id_variante, order_by=(h.alterado_em, h.id_historico_preco))
        .label("ate"),
    ).subquery()
    referencia = func.coalesce(faixas.c.preco, Variante.preco)
    consulta = (
        select(Pedido.codigo_venda, Pedido.pago_em, Unidade.nome.label("unidade"), Produto.nome.label("produto"),
               Variante.sku, ItemPedido.preco_unitario, referencia.label("preco_catalogo"),
               func.count().over().label("total"))
        .select_from(ItemPedido)
        .join(Pedido, Pedido.id_pedido == ItemPedido.id_pedido)
        .outerjoin(faixas, and_(faixas.c.id_variante == ItemPedido.id_variante, faixas.c.desde <= Pedido.pago_em,
                                or_(faixas.c.ate.is_(None), Pedido.pago_em < faixas.c.ate)))
        .join(Variante, Variante.id_variante == ItemPedido.id_variante)
        .join(Produto, Produto.id_produto == Variante.id_produto)
        .join(Unidade, Unidade.id_unidade == Pedido.id_unidade)
        .where(*_vendas(desde, ate, id_unidade, marca), referencia > 0,
               func.abs(ItemPedido.preco_unitario - referencia) > referencia * PRECO_FORA_DO_CATALOGO)
        .order_by(Pedido.pago_em.desc(), Pedido.id_pedido.desc())
        .limit(EXEMPLOS_DE_ANOMALIA)
    )
    return list(db.execute(consulta).mappings())


# pedido muito acima do normal do canal no período (mais que VEZES_A_MEDIANA a mediana, sem o frete)
def vendas_fora_do_padrao(db: Session, desde: datetime, ate: datetime, id_unidade: int | None,
                          marca: str | None) -> list:
    valor = Pedido.valor_total - Pedido.valor_frete
    medianas = (
        select(Pedido.canal_venda, func.percentile_cont(0.5).within_group(valor).label("mediana"))
        .where(*_vendas(desde, ate, id_unidade, marca))
        .group_by(Pedido.canal_venda)
        .having(func.count() >= PEDIDOS_PARA_MEDIANA)
        .subquery()
    )
    consulta = (
        select(Pedido.codigo_venda, Pedido.pago_em, Pedido.canal_venda, Unidade.nome.label("unidade"),
               valor.label("valor"), medianas.c.mediana, func.count().over().label("total"))
        .join(medianas, medianas.c.canal_venda == Pedido.canal_venda)
        .join(Unidade, Unidade.id_unidade == Pedido.id_unidade)
        .where(*_vendas(desde, ate, id_unidade, marca), valor > medianas.c.mediana * VEZES_A_MEDIANA)
        .order_by(valor.desc())
        .limit(EXEMPLOS_DE_ANOMALIA)
    )
    return list(db.execute(consulta).mappings())


# dois pedidos não cancelados do mesmo cliente (ou CPF da nota), na mesma unidade, com o mesmo valor, em até
# REPETIDO_EM segundos: pode ser venda lançada duas vezes
def pedidos_repetidos(db: Session, desde: datetime, ate: datetime, id_unidade: int | None,
                      marca: str | None) -> list:
    outro = aliased(Pedido, name="outro")
    dono = func.coalesce(func.cast(Pedido.id_cliente, String), Pedido.cpf_nota)
    dono_do_outro = func.coalesce(func.cast(outro.id_cliente, String), outro.cpf_nota)
    consulta = (
        select(Pedido.codigo_venda, outro.codigo_venda.label("repetido"), Pedido.pago_em,
               Unidade.nome.label("unidade"), Pedido.valor_total, func.count().over().label("total"))
        .join(outro, and_(outro.id_pedido > Pedido.id_pedido, outro.id_unidade == Pedido.id_unidade,
                          outro.valor_total == Pedido.valor_total, dono_do_outro == dono,
                          outro.status != "cancelado",
                          func.abs(func.extract("epoch", outro.criado_em - Pedido.criado_em)) <= REPETIDO_EM))
        .join(Unidade, Unidade.id_unidade == Pedido.id_unidade)
        .where(*_vendas(desde, ate, id_unidade, marca), dono.is_not(None))
        .order_by(Pedido.pago_em.desc())
        .limit(EXEMPLOS_DE_ANOMALIA)
    )
    return list(db.execute(consulta).mappings())


# saldo negativo agora (o banco recusa saída maior que o disponível; negativo só vindo de dado antigo ou ajuste)
def estoque_negativo(db: Session, id_unidade: int | None, marca: str | None) -> list:
    consulta = (
        select(Variante.sku, Produto.nome.label("produto"), Variante.cor, Variante.tamanho,
               Unidade.nome.label("unidade"), Estoque.canal, Estoque.quantidade, func.count().over().label("total"))
        .join(Variante, Variante.id_variante == Estoque.id_variante)
        .join(Produto, Produto.id_produto == Variante.id_produto)
        .join(Unidade, Unidade.id_unidade == Estoque.id_unidade)
        .where(Estoque.quantidade < 0)
        .order_by(Estoque.quantidade, Produto.nome)
        .limit(EXEMPLOS_DE_ANOMALIA)
    )
    if id_unidade is not None:
        consulta = consulta.where(Estoque.id_unidade == id_unidade)
    if marca is not None:
        consulta = consulta.where(Unidade.marca == marca)
    return list(db.execute(consulta).mappings())
