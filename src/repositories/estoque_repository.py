from datetime import datetime

from sqlalchemy import Integer, Select, and_, case, cast, func, literal, literal_column, or_, select, union_all
from sqlalchemy.orm import Session, aliased

from src.models.catalogo import CategoriaProduto, Produto, Variante
from src.models.contas import Usuario
from src.models.estoque import Estoque, ItemTransferencia, MovimentacaoEstoque, Transferencia, Unidade

FUSO = "America/Sao_Paulo"
UNIDADE_DO_POSTGRES = {"hora": "hour", "dia": "day", "semana": "week"}


def buscar_variante(db: Session, id_variante: int) -> Variante | None:
    return db.get(Variante, id_variante)


def buscar_unidade(db: Session, id_unidade: int) -> Unidade | None:
    return db.get(Unidade, id_unidade)


# marca do produto de cada variante: a unidade só guarda peças da própria marca
def marcas_das_variantes(db: Session, ids_variante: list[int]) -> dict[int, str]:
    consulta = (
        select(Variante.id_variante, Produto.marca)
        .join(Produto, Produto.id_produto == Variante.id_produto)
        .where(Variante.id_variante.in_(ids_variante))
    )
    return dict(db.execute(consulta).all())


def buscar_estoque(db: Session, id_variante: int, id_unidade: int, canal: str) -> Estoque | None:
    return db.get(Estoque, (id_variante, id_unidade, canal))


# trava as linhas de ESTOQUE antes de ler o disponível; sempre na mesma ordem (por canal),
# para duas operações ao mesmo tempo não se travarem uma à outra (case, seção 2)
def travar_estoque(db: Session, id_variante: int, id_unidade: int, canais: list[str]) -> dict[str, Estoque]:
    consulta = (
        select(Estoque)
        .where(Estoque.id_variante == id_variante, Estoque.id_unidade == id_unidade, Estoque.canal.in_(canais))
        .order_by(Estoque.canal)
        .with_for_update()
    )
    return {linha.canal: linha for linha in db.scalars(consulta)}


# o trigger aplica a movimentação no saldo (ADR 0005); o refresh traz criado_em do banco
def inserir_movimentacao(db: Session, **campos) -> MovimentacaoEstoque:
    movimentacao = MovimentacaoEstoque(**campos)
    db.add(movimentacao)
    db.flush()
    db.refresh(movimentacao)
    return movimentacao


def adicionar_estoque(db: Session, estoque: Estoque) -> None:
    db.add(estoque)
    db.flush()


def _paginar(db: Session, consulta: Select, limit: int, offset: int) -> tuple[list, int]:
    total = db.scalar(select(func.count()).select_from(consulta.order_by(None).subquery()))
    linhas = db.execute(consulta.limit(limit).offset(offset)).mappings().all()
    return list(linhas), total


# a marca é a da unidade (a mesma regra da visão geral); as consultas abaixo já juntam a UNIDADE
def _filtros_comuns(colunas, id_unidade, id_variante, canal, busca, marca=None) -> list:
    filtros = []
    if marca is not None:
        filtros.append(Unidade.marca == marca)
    if id_unidade is not None:
        filtros.append(colunas["id_unidade"] == id_unidade)
    if id_variante is not None:
        filtros.append(colunas["id_variante"] == id_variante)
    if canal is not None:
        filtros.append(colunas["canal"] == canal)
    if busca:
        padrao = f"%{busca}%"
        # o leitor de código de barras manda o EAN inteiro: só o código exato acha a peça
        filtros.append(or_(Variante.sku.ilike(padrao), Produto.nome.ilike(padrao), Variante.ean == busca.strip()))
    return filtros


# ---------- estoque atual ----------

def _abaixo_do_minimo():
    return and_(Estoque.estoque_minimo.is_not(None), Estoque.quantidade < Estoque.estoque_minimo)


def consulta_estoque(id_unidade=None, id_variante=None, canal=None, busca=None, abaixo_minimo=None,
                     marca=None) -> Select:
    disponivel = Estoque.quantidade - Estoque.quantidade_reservada
    abaixo = _abaixo_do_minimo()
    consulta = (
        select(
            Estoque.id_variante, Variante.sku, Produto.nome.label("produto"), Variante.cor, Variante.tamanho,
            Estoque.id_unidade, Unidade.nome.label("unidade"), Estoque.canal, Estoque.quantidade,
            Estoque.quantidade_reservada, disponivel.label("disponivel"), Estoque.estoque_minimo,
            abaixo.label("abaixo_minimo"),
        )
        .join(Variante, Variante.id_variante == Estoque.id_variante)
        .join(Produto, Produto.id_produto == Variante.id_produto)
        .join(Unidade, Unidade.id_unidade == Estoque.id_unidade)
        .order_by(Produto.nome, Variante.cor, Variante.tamanho, Unidade.nome, Estoque.canal)
    )
    colunas = {"id_unidade": Estoque.id_unidade, "id_variante": Estoque.id_variante, "canal": Estoque.canal}
    filtros = _filtros_comuns(colunas, id_unidade, id_variante, canal, busca, marca)
    if abaixo_minimo is not None:
        filtros.append(abaixo if abaixo_minimo else ~abaixo)
    return consulta.where(*filtros)


def listar_estoque(db: Session, limit: int, offset: int, **filtros) -> tuple[list, int]:
    return _paginar(db, consulta_estoque(**filtros), limit, offset)


# a página de produtos da tela Estoque: com os mesmos filtros, escolhe os produtos e o total numa consulta
# (só_abaixo: produtos com alguma linha abaixo do mínimo). Agrupa pelo nome, como a tela
def consulta_produtos_do_estoque(so_abaixo=False, **filtros) -> Select:
    consulta = (
        consulta_estoque(**filtros)
        .with_only_columns(Produto.nome, func.count().over().label("total"))
        .group_by(Produto.nome)
        .order_by(None)
        .order_by(Produto.nome)
    )
    return consulta.having(func.bool_or(_abaixo_do_minimo())) if so_abaixo else consulta


# devolve as linhas (variante, unidade e canal) só dos produtos da página, e o total de produtos
def listar_estoque_por_produto(db: Session, limit: int, offset: int, so_abaixo=False, **filtros) -> tuple[list, int]:
    produtos = db.execute(consulta_produtos_do_estoque(so_abaixo, **filtros).limit(limit).offset(offset)).all()
    if not produtos:
        total = db.scalar(select(func.count()).select_from(consulta_produtos_do_estoque(so_abaixo, **filtros).subquery()))
        return [], total
    consulta = consulta_estoque(**filtros).where(Produto.nome.in_([p.nome for p in produtos]))
    return list(db.execute(consulta).mappings().all()), produtos[0].total


# ---------- histórico: soma das movimentações até a data (ADR 0006) ----------

def consulta_historico(em: datetime, id_unidade=None, id_variante=None, canal=None, busca=None,
                       marca=None) -> Select:
    m = MovimentacaoEstoque
    consulta = (
        select(
            m.id_variante, Variante.sku, Produto.nome.label("produto"), Variante.cor, Variante.tamanho,
            m.id_unidade, Unidade.nome.label("unidade"), m.canal, func.sum(m.quantidade).label("quantidade"),
        )
        .join(Variante, Variante.id_variante == m.id_variante)
        .join(Produto, Produto.id_produto == Variante.id_produto)
        .join(Unidade, Unidade.id_unidade == m.id_unidade)
        .where(m.criado_em <= em)
        .group_by(m.id_variante, Variante.sku, Produto.nome, Variante.cor, Variante.tamanho,
                  m.id_unidade, Unidade.nome, m.canal)
        .order_by(Produto.nome, Variante.cor, Variante.tamanho, Unidade.nome, m.canal)
    )
    colunas = {"id_unidade": m.id_unidade, "id_variante": m.id_variante, "canal": m.canal}
    return consulta.where(*_filtros_comuns(colunas, id_unidade, id_variante, canal, busca, marca))


def listar_historico(db: Session, em: datetime, limit: int, offset: int, **filtros) -> tuple[list, int]:
    return _paginar(db, consulta_historico(em, **filtros), limit, offset)


# ---------- histórico comparado com agora: uma linha por peça e unidade, somando os canais do filtro ----------

ORDEM_TAMANHOS = ["PP", "P", "M", "G", "GG", "XG", "U"]


# a ordem da grade (PP, P, M...), depois os números (36, 38...), depois o resto em ordem alfabética
def _ordem_do_tamanho(tamanho) -> list:
    grade = case({t: i for i, t in enumerate(ORDEM_TAMANHOS)}, value=func.upper(tamanho), else_=len(ORDEM_TAMANHOS))
    numero = case((tamanho.regexp_match(r"^\d+$"), cast(tamanho, Integer)))
    return [grade, numero, tamanho]


def consulta_comparacao(em: datetime, id_unidade=None, canal=None, busca=None, so_mudou=False,
                        marca=None) -> Select:
    m = MovimentacaoEstoque
    filtros_m, filtros_e = [m.criado_em <= em], []
    if id_unidade is not None:
        filtros_m.append(m.id_unidade == id_unidade)
        filtros_e.append(Estoque.id_unidade == id_unidade)
    if canal is not None:
        filtros_m.append(m.canal == canal)
        filtros_e.append(Estoque.canal == canal)
    # as movimentações até o momento (naquele) e o saldo de agora numa lista só, somadas por ids;
    # os nomes entram depois: ordenar texto antes de agrupar era o passo mais lento
    linhas = union_all(
        select(m.id_variante, m.id_unidade, m.quantidade.label("naquele"), literal(0).label("agora"))
        .where(*filtros_m),
        select(Estoque.id_variante, Estoque.id_unidade, literal(0), Estoque.quantidade).where(*filtros_e),
    ).subquery()
    por_peca = (
        select(linhas.c.id_variante, linhas.c.id_unidade, func.sum(linhas.c.naquele).label("naquele"),
               func.sum(linhas.c.agora).label("agora"))
        .group_by(linhas.c.id_variante, linhas.c.id_unidade)
        .subquery()
    )
    mudou = por_peca.c.naquele != por_peca.c.agora
    consulta = (
        select(
            por_peca.c.id_variante, Variante.sku, Produto.nome.label("produto"), Variante.cor, Variante.tamanho,
            por_peca.c.id_unidade, Unidade.nome.label("unidade"), por_peca.c.naquele, por_peca.c.agora,
            # o total e quantas mudaram vêm na mesma consulta, sem contar de novo
            func.count().over().label("total"), func.count().filter(mudou).over().label("mudaram"),
        )
        .join(Variante, Variante.id_variante == por_peca.c.id_variante)
        .join(Produto, Produto.id_produto == Variante.id_produto)
        .join(Unidade, Unidade.id_unidade == por_peca.c.id_unidade)
        .order_by(Produto.nome, Variante.cor, *_ordem_do_tamanho(Variante.tamanho), Unidade.nome)
    )
    colunas = {"id_unidade": por_peca.c.id_unidade, "id_variante": por_peca.c.id_variante, "canal": None}
    filtros = _filtros_comuns(colunas, None, None, None, busca, marca)
    if so_mudou:
        filtros.append(mudou)
    return consulta.where(*filtros)


# devolve a página, o total e quantas mudaram (com "só o que mudou", total e mudaram são iguais)
def listar_comparacao(db: Session, em: datetime, limit: int, offset: int, **filtros) -> tuple[list, int, int]:
    consulta = consulta_comparacao(em, **filtros)
    linhas = db.execute(consulta.limit(limit).offset(offset)).mappings().all()
    if not linhas and offset > 0:
        # página além do fim (a lista mudou entre uma página e outra): a primeira linha traz os totais
        primeira = db.execute(consulta.limit(1)).mappings().first()
        return [], primeira["total"] if primeira else 0, primeira["mudaram"] if primeira else 0
    total, mudaram = (linhas[0]["total"], linhas[0]["mudaram"]) if linhas else (0, 0)
    return [dict(linha) for linha in linhas], total, mudaram


# ---------- peças paradas: com saldo e sem venda há tempo (assistente interno) ----------

# o "normal" de cada categoria: o tempo entre uma venda e a próxima da mesma peça na mesma loja, nas vendas desde
# "desde", no ponto que cobre 9 de cada 10 casos (percentil 90). Devolve {id_categoria: (nome, dias, intervalos)}
def prazos_por_categoria(db: Session, desde: datetime) -> dict[int, tuple[str, float, int]]:
    m = MovimentacaoEstoque
    vendas = (
        select(m.id_variante, m.criado_em,
               func.lag(m.criado_em).over(partition_by=(m.id_variante, m.id_unidade), order_by=m.criado_em)
               .label("anterior"))
        .join(Unidade, Unidade.id_unidade == m.id_unidade)
        .where(m.tipo == "venda", Unidade.tipo == "loja", m.criado_em >= desde)
        .subquery()
    )
    dias = func.extract("epoch", vendas.c.criado_em - vendas.c.anterior) / 86400
    consulta = (
        select(CategoriaProduto.id_categoria, CategoriaProduto.nome,
               func.percentile_cont(0.9).within_group(dias).label("dias"), func.count().label("intervalos"))
        .select_from(vendas)
        .join(Variante, Variante.id_variante == vendas.c.id_variante)
        .join(Produto, Produto.id_produto == Variante.id_produto)
        .join(CategoriaProduto, CategoriaProduto.id_categoria == Produto.id_categoria)
        .where(vendas.c.anterior.is_not(None))
        .group_by(CategoriaProduto.id_categoria, CategoriaProduto.nome)
    )
    return {l.id_categoria: (l.nome, float(l.dias), l.intervalos) for l in db.execute(consulta)}


# cada peça numa unidade com saldo hoje, com a última venda ali e a primeira movimentação ali (a chegada):
# parada = desde a última venda, ou desde a chegada se nunca vendeu ali, antes do corte. O corte pode ser um só
# ou um por categoria (cortes_por_categoria; a categoria sem corte usa o "corte"). Com o total de linhas
# e de peças paradas na mesma consulta; as mais antigas e com mais peças primeiro
def consulta_pecas_paradas(corte: datetime, id_unidade=None, marca=None,
                           cortes_por_categoria: dict[int, datetime] | None = None) -> Select:
    m = MovimentacaoEstoque
    saldo = (
        select(Estoque.id_variante, Estoque.id_unidade, func.sum(Estoque.quantidade).label("quantidade"))
        .group_by(Estoque.id_variante, Estoque.id_unidade)
        .having(func.sum(Estoque.quantidade) > 0)
    )
    if id_unidade is not None:
        saldo = saldo.where(Estoque.id_unidade == id_unidade)
    saldo = saldo.subquery()
    datas = (
        select(m.id_variante, m.id_unidade,
               func.max(m.criado_em).filter(m.tipo == "venda").label("ultima_venda"),
               func.min(m.criado_em).label("chegou_em"))
        .group_by(m.id_variante, m.id_unidade)
        .subquery()
    )
    desde = func.coalesce(datas.c.ultima_venda, datas.c.chegou_em)
    limite = case(cortes_por_categoria, value=Produto.id_categoria, else_=corte) if cortes_por_categoria else corte
    consulta = (
        select(
            saldo.c.id_variante, Variante.sku, Produto.nome.label("produto"), Produto.id_categoria, Variante.cor,
            Variante.tamanho, saldo.c.id_unidade, Unidade.nome.label("unidade"), saldo.c.quantidade,
            datas.c.ultima_venda, desde.label("parada_desde"), func.count().over().label("total_linhas"),
            func.sum(saldo.c.quantidade).over().label("total_pecas"),
        )
        .join(datas, and_(datas.c.id_variante == saldo.c.id_variante, datas.c.id_unidade == saldo.c.id_unidade))
        .join(Variante, Variante.id_variante == saldo.c.id_variante)
        .join(Produto, Produto.id_produto == Variante.id_produto)
        .join(Unidade, Unidade.id_unidade == saldo.c.id_unidade)
        .where(desde < limite)
        .order_by(desde, saldo.c.quantidade.desc(), Produto.nome, Variante.cor, Variante.tamanho)
    )
    if marca is not None:
        consulta = consulta.where(Unidade.marca == marca)
    return consulta


def _filtro_variante_unidade(id_variante: int, id_unidade: int | None) -> list:
    m = MovimentacaoEstoque
    filtros = [m.id_variante == id_variante]
    if id_unidade is not None:
        filtros.append(m.id_unidade == id_unidade)
    return filtros


# saldo de cada canal logo antes do início do gráfico
def saldo_antes(db: Session, id_variante: int, id_unidade: int | None, inicio: datetime) -> dict[str, int]:
    m = MovimentacaoEstoque
    consulta = (
        select(m.canal, func.sum(m.quantidade))
        .where(*_filtro_variante_unidade(id_variante, id_unidade), m.criado_em < inicio)
        .group_by(m.canal)
    )
    return {canal: int(soma) for canal, soma in db.execute(consulta)}


def consulta_somas_por_periodo(
    id_variante: int, id_unidade: int | None, inicio: datetime, fim: datetime, granularidade: str
) -> Select:
    m = MovimentacaoEstoque
    # valores escritos no SQL (vêm de listas fixas do código), para o GROUP BY ser idêntico ao SELECT
    unidade = literal_column(f"'{UNIDADE_DO_POSTGRES[granularidade]}'")
    periodo = func.date_trunc(unidade, func.timezone(literal_column(f"'{FUSO}'"), m.criado_em)).label("periodo")
    return (
        select(periodo, m.canal, func.sum(m.quantidade).label("soma"))
        .where(*_filtro_variante_unidade(id_variante, id_unidade), m.criado_em >= inicio, m.criado_em <= fim)
        .group_by(periodo, m.canal)
        .order_by(periodo)
    )


# soma das movimentações em cada período (hora, dia ou semana no horário de Brasília)
def somas_por_periodo(
    db: Session, id_variante: int, id_unidade: int | None, inicio: datetime, fim: datetime, granularidade: str
) -> list[tuple[datetime, str, int]]:
    consulta = consulta_somas_por_periodo(id_variante, id_unidade, inicio, fim, granularidade)
    return [(periodo, canal, int(soma)) for periodo, canal, soma in db.execute(consulta)]


# ---------- movimentações ----------

def consulta_movimentacoes(
    id_unidade=None, id_variante=None, canal=None, tipo=None, de=None, ate=None, busca=None, marca=None
) -> Select:
    m = MovimentacaoEstoque
    consulta = (
        select(
            *m.__table__.columns, Variante.sku, Produto.nome.label("produto"), Unidade.nome.label("unidade"),
            Usuario.nome.label("autor"),
        )
        .join(Variante, Variante.id_variante == m.id_variante)
        .join(Produto, Produto.id_produto == Variante.id_produto)
        .join(Unidade, Unidade.id_unidade == m.id_unidade)
        .outerjoin(Usuario, Usuario.id_usuario == m.id_usuario)
        .order_by(m.criado_em.desc(), m.id_movimentacao.desc())
    )
    colunas = {"id_unidade": m.id_unidade, "id_variante": m.id_variante, "canal": m.canal}
    filtros = _filtros_comuns(colunas, id_unidade, id_variante, canal, busca, marca)
    if tipo is not None:
        filtros.append(m.tipo == tipo)
    if de is not None:
        filtros.append(m.criado_em >= de)
    if ate is not None:
        filtros.append(m.criado_em <= ate)
    return consulta.where(*filtros)


def listar_movimentacoes(db: Session, limit: int, offset: int, **filtros) -> tuple[list, int]:
    return _paginar(db, consulta_movimentacoes(**filtros), limit, offset)


# ---------- divergência: saldo x soma das movimentações (ADR 0005) ----------

def consulta_divergencias(marca=None) -> Select:
    m = MovimentacaoEstoque
    somas = (
        select(m.id_variante, m.id_unidade, m.canal, func.sum(m.quantidade).label("soma"))
        .group_by(m.id_variante, m.id_unidade, m.canal)
        .subquery()
    )
    soma = func.coalesce(somas.c.soma, 0)
    consulta = (
        select(Estoque.id_variante, Estoque.id_unidade, Estoque.canal, Estoque.quantidade,
               soma.label("soma_movimentacoes"))
        .outerjoin(somas, and_(
            somas.c.id_variante == Estoque.id_variante,
            somas.c.id_unidade == Estoque.id_unidade,
            somas.c.canal == Estoque.canal,
        ))
        .where(Estoque.quantidade != soma)
        .order_by(Estoque.id_unidade, Estoque.id_variante, Estoque.canal)
    )
    if marca is not None:
        consulta = consulta.where(Estoque.id_unidade.in_(select(Unidade.id_unidade).where(Unidade.marca == marca)))
    return consulta


def listar_divergencias(db: Session, marca: str | None = None) -> list:
    return list(db.execute(consulta_divergencias(marca)).mappings().all())


# ---------- peças em trânsito: enviadas e ainda não recebidas naquele momento ----------

Origem = aliased(Unidade, name="origem")
Destino = aliased(Unidade, name="destino")


def consulta_em_transito(em: datetime, id_variante=None, id_unidade=None, busca=None, marca=None) -> Select:
    i, t = ItemTransferencia, Transferencia
    consulta = (
        select(
            t.id_transferencia, i.id_variante, Variante.sku, Produto.nome.label("produto"), Variante.cor,
            Variante.tamanho, t.id_unidade_origem, Origem.nome.label("origem"), t.id_unidade_destino,
            Destino.nome.label("destino"), i.canal_entrada, i.quantidade_enviada.label("quantidade"), t.enviada_em,
        )
        .join(t, t.id_transferencia == i.id_transferencia)
        .join(Variante, Variante.id_variante == i.id_variante)
        .join(Produto, Produto.id_produto == Variante.id_produto)
        .join(Origem, Origem.id_unidade == t.id_unidade_origem)
        .join(Destino, Destino.id_unidade == t.id_unidade_destino)
        # a saída já aconteceu e a entrada ainda não (no recebimento entra o total enviado)
        .where(t.enviada_em <= em, or_(t.recebida_em.is_(None), t.recebida_em > em), i.quantidade_enviada > 0)
        .order_by(t.enviada_em, t.id_transferencia, Produto.nome, Variante.cor, Variante.tamanho)
    )
    if id_variante is not None:
        consulta = consulta.where(i.id_variante == id_variante)
    if id_unidade is not None:
        consulta = consulta.where(or_(t.id_unidade_origem == id_unidade, t.id_unidade_destino == id_unidade))
    if busca:
        padrao = f"%{busca}%"
        consulta = consulta.where(or_(Variante.sku.ilike(padrao), Produto.nome.ilike(padrao)))
    # transferência só acontece dentro da mesma marca: a da origem basta
    if marca is not None:
        consulta = consulta.where(Origem.marca == marca)
    return consulta


def listar_em_transito(db: Session, em: datetime, **filtros) -> list:
    return list(db.execute(consulta_em_transito(em, **filtros)).mappings().all())
