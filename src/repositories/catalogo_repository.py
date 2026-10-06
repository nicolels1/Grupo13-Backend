from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.models.catalogo import CategoriaProduto, HistoricoPreco, Produto, Variante


def listar_categorias(db: Session, ativo: bool | None = None) -> list[CategoriaProduto]:
    consulta = select(CategoriaProduto).order_by(CategoriaProduto.nome)
    if ativo is not None:
        consulta = consulta.where(CategoriaProduto.ativo == ativo)
    return list(db.scalars(consulta))


def buscar_categoria(db: Session, id_categoria: int) -> CategoriaProduto | None:
    return db.get(CategoriaProduto, id_categoria)


# comparação sem diferenciar maiúsculas: "Camisas" e "camisas" são a mesma categoria
def categoria_por_nome(db: Session, nome: str) -> CategoriaProduto | None:
    return db.scalar(select(CategoriaProduto).where(func.lower(CategoriaProduto.nome) == nome.lower()))


# ---------- produto e variante ----------

# uma página de produtos e o total; busca no nome sem diferenciar maiúsculas
# categoria_ativa=True: só produtos de categorias ativas (visão pública)
def listar_produtos(db: Session, limit: int, offset: int, id_categoria: int | None = None,
                    ativo: bool | None = None, busca: str | None = None,
                    categoria_ativa: bool | None = None) -> tuple[list[Produto], int]:
    consulta = select(Produto)
    if categoria_ativa is not None:
        consulta = consulta.join(CategoriaProduto, CategoriaProduto.id_categoria == Produto.id_categoria).where(
            CategoriaProduto.ativo == categoria_ativa
        )
    if id_categoria is not None:
        consulta = consulta.where(Produto.id_categoria == id_categoria)
    if ativo is not None:
        consulta = consulta.where(Produto.ativo == ativo)
    if busca:
        consulta = consulta.where(Produto.nome.ilike(f"%{busca}%"))
    total = db.scalar(select(func.count()).select_from(consulta.subquery()))
    pagina = consulta.order_by(Produto.nome, Produto.id_produto).limit(limit).offset(offset)
    return list(db.scalars(pagina)), total


def buscar_produto(db: Session, id_produto: int) -> Produto | None:
    return db.get(Produto, id_produto)


# variantes de vários produtos numa consulta só, agrupadas por produto
def variantes_dos_produtos(db: Session, ids_produto: list[int]) -> dict[int, list[Variante]]:
    agrupadas: dict[int, list[Variante]] = {i: [] for i in ids_produto}
    if ids_produto:
        consulta = (
            select(Variante)
            .where(Variante.id_produto.in_(ids_produto))
            .order_by(Variante.cor, Variante.tamanho, Variante.id_variante)
        )
        for variante in db.scalars(consulta):
            agrupadas[variante.id_produto].append(variante)
    return agrupadas


def buscar_variante(db: Session, id_variante: int) -> Variante | None:
    return db.get(Variante, id_variante)


def variante_por_sku(db: Session, sku: str) -> Variante | None:
    return db.scalar(select(Variante).where(func.upper(Variante.sku) == sku.upper()))


def variante_por_cor_e_tamanho(db: Session, id_produto: int, cor: str, tamanho: str) -> Variante | None:
    return db.scalar(select(Variante).where(
        Variante.id_produto == id_produto,
        func.lower(Variante.cor) == cor.lower(),
        func.lower(Variante.tamanho) == tamanho.lower(),
    ))


# histórico completo (mais recente primeiro) ou só o registro que valia na data `em`
def historico_preco(db: Session, id_variante: int, em: datetime | None = None) -> list[HistoricoPreco]:
    consulta = select(HistoricoPreco).where(HistoricoPreco.id_variante == id_variante)
    if em is not None:
        consulta = consulta.where(HistoricoPreco.alterado_em <= em)
    consulta = consulta.order_by(HistoricoPreco.alterado_em.desc(), HistoricoPreco.id_historico_preco.desc())
    if em is not None:
        consulta = consulta.limit(1)
    return list(db.scalars(consulta))
