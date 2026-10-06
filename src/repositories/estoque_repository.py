from datetime import datetime

from sqlalchemy import Select, and_, func, literal_column, or_, select
from sqlalchemy.orm import Session, aliased

from src.models.catalogo import Produto, Variante
from src.models.contas import Usuario
from src.models.estoque import Estoque, ItemTransferencia, MovimentacaoEstoque, Transferencia, Unidade

FUSO = "America/Sao_Paulo"
UNIDADE_DO_POSTGRES = {"hora": "hour", "dia": "day", "semana": "week"}


def buscar_variante(db: Session, id_variante: int) -> Variante | None:
    return db.get(Variante, id_variante)


def buscar_unidade(db: Session, id_unidade: int) -> Unidade | None:
    return db.get(Unidade, id_unidade)


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


def _filtros_comuns(colunas, id_unidade, id_variante, canal, busca) -> list:
    filtros = []
    if id_unidade is not None:
        filtros.append(colunas["id_unidade"] == id_unidade)
    if id_variante is not None:
        filtros.append(colunas["id_variante"] == id_variante)
    if canal is not None:
        filtros.append(colunas["canal"] == canal)
    if busca:
        padrao = f"%{busca}%"
        filtros.append(or_(Variante.sku.ilike(padrao), Produto.nome.ilike(padrao)))
    return filtros


# ---------- estoque atual ----------

def consulta_estoque(id_unidade=None, id_variante=None, canal=None, busca=None, abaixo_minimo=None) -> Select:
    disponivel = Estoque.quantidade - Estoque.quantidade_reservada
    abaixo = and_(Estoque.estoque_minimo.is_not(None), Estoque.quantidade < Estoque.estoque_minimo)
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
    filtros = _filtros_comuns(colunas, id_unidade, id_variante, canal, busca)
    if abaixo_minimo is not None:
        filtros.append(abaixo if abaixo_minimo else ~abaixo)
    return consulta.where(*filtros)


def listar_estoque(db: Session, limit: int, offset: int, **filtros) -> tuple[list, int]:
    return _paginar(db, consulta_estoque(**filtros), limit, offset)


# ---------- histórico: soma das movimentações até a data (ADR 0006) ----------

def consulta_historico(em: datetime, id_unidade=None, id_variante=None, canal=None, busca=None) -> Select:
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
    return consulta.where(*_filtros_comuns(colunas, id_unidade, id_variante, canal, busca))


def listar_historico(db: Session, em: datetime, limit: int, offset: int, **filtros) -> tuple[list, int]:
    return _paginar(db, consulta_historico(em, **filtros), limit, offset)


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
    id_unidade=None, id_variante=None, canal=None, tipo=None, de=None, ate=None, busca=None
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
    filtros = _filtros_comuns(colunas, id_unidade, id_variante, canal, busca)
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

def consulta_divergencias() -> Select:
    m = MovimentacaoEstoque
    somas = (
        select(m.id_variante, m.id_unidade, m.canal, func.sum(m.quantidade).label("soma"))
        .group_by(m.id_variante, m.id_unidade, m.canal)
        .subquery()
    )
    soma = func.coalesce(somas.c.soma, 0)
    return (
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


def listar_divergencias(db: Session) -> list:
    return list(db.execute(consulta_divergencias()).mappings().all())


# ---------- peças em trânsito: enviadas e ainda não recebidas naquele momento ----------

Origem = aliased(Unidade, name="origem")
Destino = aliased(Unidade, name="destino")


def consulta_em_transito(em: datetime, id_variante=None, id_unidade=None, busca=None) -> Select:
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
    return consulta


def listar_em_transito(db: Session, em: datetime, **filtros) -> list:
    return list(db.execute(consulta_em_transito(em, **filtros)).mappings().all())
