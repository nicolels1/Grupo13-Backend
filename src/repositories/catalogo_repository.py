from datetime import datetime

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from src.models.catalogo import CategoriaProduto, HistoricoPreco, ImagemProduto, Produto, Variante
from src.models.estoque import Estoque, Unidade


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

# variantes ativas com peça para vender online: estoque menos o reservado no canal online de uma
# unidade ativa, a mesma conta do checkout. A vitrine física não entra: o site não vende dela
def _variantes_disponiveis_online(tamanhos: list[str] | None = None) -> Select:
    consulta = (
        select(Variante.id_variante, Variante.id_produto)
        .join(Estoque, Estoque.id_variante == Variante.id_variante)
        .join(Unidade, Unidade.id_unidade == Estoque.id_unidade)
        .where(Variante.ativo, Unidade.ativo, Estoque.canal == "online",
               Estoque.quantidade - Estoque.quantidade_reservada > 0)
    )
    if tamanhos:
        consulta = consulta.where(Variante.tamanho.in_(tamanhos))
    return consulta


def ids_disponiveis_online(db: Session, ids_variante: list[int]) -> set[int]:
    if not ids_variante:
        return set()
    consulta = _variantes_disponiveis_online().where(Variante.id_variante.in_(ids_variante))
    return {linha.id_variante for linha in db.execute(consulta)}


# preço "a partir de" do produto: a variante ativa mais barata
_MENOR_PRECO = (
    select(func.min(Variante.preco))
    .where(Variante.id_produto == Produto.id_produto, Variante.ativo)
    .correlate(Produto)
    .scalar_subquery()
)

# novidades: sem data de cadastro no produto, o número maior é o mais novo
ORDENS = {
    "nome": (Produto.nome, Produto.id_produto),
    "novidades": (Produto.id_produto.desc(),),
    "menor_preco": (_MENOR_PRECO.asc().nulls_last(), Produto.nome, Produto.id_produto),
    "maior_preco": (_MENOR_PRECO.desc().nulls_last(), Produto.nome, Produto.id_produto),
}


# uma página de produtos e o total; busca no nome sem diferenciar maiúsculas
# categoria_ativa=True: só produtos de categorias ativas (visão pública)
# tamanhos: com variante ativa em algum desses tamanhos; disponivel: com peça para vender online
# (em algum desses tamanhos, se houver)
def listar_produtos(db: Session, limit: int, offset: int, id_categoria: int | None = None,
                    ativo: bool | None = None, busca: str | None = None,
                    categoria_ativa: bool | None = None, tamanhos: list[str] | None = None,
                    disponivel: bool | None = None, ordem: str = "nome") -> tuple[list[Produto], int]:
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
    if tamanhos:
        consulta = consulta.where(Produto.id_produto.in_(
            select(Variante.id_produto).where(Variante.tamanho.in_(tamanhos), Variante.ativo)
        ))
    if disponivel is not None:
        com_peca = Produto.id_produto.in_(_variantes_disponiveis_online(tamanhos).with_only_columns(Variante.id_produto))
        consulta = consulta.where(com_peca if disponivel else ~com_peca)
    total = db.scalar(select(func.count()).select_from(consulta.subquery()))
    pagina = consulta.order_by(*ORDENS[ordem]).limit(limit).offset(offset)
    return list(db.scalars(pagina)), total


# tamanhos distintos das variantes à venda na vitrine (variante, produto e categoria ativos)
def tamanhos_a_venda(db: Session, id_categoria: int | None = None, busca: str | None = None) -> list[str]:
    consulta = (
        select(Variante.tamanho).distinct()
        .join(Produto, Produto.id_produto == Variante.id_produto)
        .join(CategoriaProduto, CategoriaProduto.id_categoria == Produto.id_categoria)
        .where(Variante.ativo, Produto.ativo, CategoriaProduto.ativo)
    )
    if id_categoria is not None:
        consulta = consulta.where(Produto.id_categoria == id_categoria)
    if busca:
        consulta = consulta.where(Produto.nome.ilike(f"%{busca}%"))
    return list(db.scalars(consulta))


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


def imagens_dos_produtos(db: Session, ids_produto: list[int]) -> dict[int, list[ImagemProduto]]:
    agrupadas: dict[int, list[ImagemProduto]] = {i: [] for i in ids_produto}
    if ids_produto:
        consulta = (
            select(ImagemProduto)
            .where(ImagemProduto.id_produto.in_(ids_produto))
            .order_by(ImagemProduto.ordem, ImagemProduto.id_imagem)
        )
        for imagem in db.scalars(consulta):
            agrupadas[imagem.id_produto].append(imagem)
    return agrupadas


def buscar_imagem(db: Session, id_imagem: int) -> ImagemProduto | None:
    return db.get(ImagemProduto, id_imagem)


def proxima_ordem_de_imagem(db: Session, id_produto: int) -> int:
    maior = db.scalar(select(func.max(ImagemProduto.ordem)).where(ImagemProduto.id_produto == id_produto))
    return (maior or 0) + 1


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
