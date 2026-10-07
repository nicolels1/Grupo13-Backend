import uuid

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, aliased

from src.models.avaliacoes import Avaliacao, DenunciaAvaliacao, FotoAvaliacao, VotoUtil
from src.models.catalogo import Produto, Variante
from src.models.contas import Usuario
from src.models.vendas import ItemPedido, Pedido

Autor = aliased(Usuario, name="autor")


def buscar_avaliacao(db: Session, id_avaliacao: int, travar: bool = False) -> Avaliacao | None:
    consulta = select(Avaliacao).where(Avaliacao.id_avaliacao == id_avaliacao)
    if travar:
        consulta = consulta.with_for_update()
    return db.scalar(consulta.execution_options(populate_existing=True))


def item_com_pedido(db: Session, id_item: int) -> tuple[ItemPedido, Pedido] | None:
    linha = db.execute(
        select(ItemPedido, Pedido).join(Pedido, Pedido.id_pedido == ItemPedido.id_pedido).where(
            ItemPedido.id_item == id_item)
    ).first()
    return None if linha is None else (linha[0], linha[1])


def avaliacao_do_item(db: Session, id_item: int) -> Avaliacao | None:
    return db.scalar(select(Avaliacao).where(Avaliacao.id_item_pedido == id_item))


# quem avaliou: o cliente do pedido do item avaliado
def autor_da_avaliacao(db: Session, id_avaliacao: int) -> uuid.UUID | None:
    return db.scalar(
        select(Pedido.id_cliente)
        .join(ItemPedido, ItemPedido.id_pedido == Pedido.id_pedido)
        .join(Avaliacao, Avaliacao.id_item_pedido == ItemPedido.id_item)
        .where(Avaliacao.id_avaliacao == id_avaliacao)
    )


def _contagem(modelo, *condicoes):
    return select(func.count()).select_from(modelo).where(*condicoes).correlate(Avaliacao).scalar_subquery()


def consulta_avaliacoes(id_avaliacao=None, id_produto=None, status=None, com_denuncia_pendente=None) -> Select:
    pendentes = _contagem(
        DenunciaAvaliacao, DenunciaAvaliacao.id_avaliacao == Avaliacao.id_avaliacao,
        DenunciaAvaliacao.status == "pendente",
    )
    consulta = (
        select(
            *Avaliacao.__table__.columns, Variante.id_produto, Produto.nome.label("produto"), Variante.cor,
            Variante.tamanho, Autor.nome.label("autor"),
            _contagem(VotoUtil, VotoUtil.id_avaliacao == Avaliacao.id_avaliacao).label("votos_util"),
            pendentes.label("denuncias_pendentes"),
        )
        .join(ItemPedido, ItemPedido.id_item == Avaliacao.id_item_pedido)
        .join(Pedido, Pedido.id_pedido == ItemPedido.id_pedido)
        .join(Autor, Autor.id_usuario == Pedido.id_cliente)
        .join(Variante, Variante.id_variante == ItemPedido.id_variante)
        .join(Produto, Produto.id_produto == Variante.id_produto)
        .order_by(Avaliacao.criada_em.desc(), Avaliacao.id_avaliacao.desc())
    )
    filtros = []
    for coluna, valor in [(Avaliacao.id_avaliacao, id_avaliacao), (Variante.id_produto, id_produto),
                          (Avaliacao.status, status)]:
        if valor is not None:
            filtros.append(coluna == valor)
    if com_denuncia_pendente is not None:
        filtros.append(pendentes > 0 if com_denuncia_pendente else pendentes == 0)
    return consulta.where(*filtros)


def listar_avaliacoes(db: Session, limit: int, offset: int, **filtros) -> tuple[list, int]:
    consulta = consulta_avaliacoes(**filtros)
    total = db.scalar(select(func.count()).select_from(consulta.order_by(None).subquery()))
    return list(db.execute(consulta.limit(limit).offset(offset)).mappings().all()), total


def detalhar_avaliacao(db: Session, id_avaliacao: int):
    return db.execute(consulta_avaliacoes(id_avaliacao=id_avaliacao)).mappings().first()


def media_do_produto(db: Session, id_produto: int):
    return db.scalar(
        select(func.round(func.avg(Avaliacao.nota), 1))
        .join(ItemPedido, ItemPedido.id_item == Avaliacao.id_item_pedido)
        .join(Variante, Variante.id_variante == ItemPedido.id_variante)
        .where(Variante.id_produto == id_produto, Avaliacao.status == "publicada")
    )


def fotos_das_avaliacoes(db: Session, ids: list[int]) -> dict[int, list[FotoAvaliacao]]:
    agrupadas: dict[int, list[FotoAvaliacao]] = {i: [] for i in ids}
    if ids:
        consulta = select(FotoAvaliacao).where(FotoAvaliacao.id_avaliacao.in_(ids)).order_by(FotoAvaliacao.ordem)
        for foto in db.scalars(consulta):
            agrupadas[foto.id_avaliacao].append(foto)
    return agrupadas


def buscar_voto(db: Session, id_avaliacao: int, id_cliente: uuid.UUID) -> VotoUtil | None:
    return db.get(VotoUtil, (id_avaliacao, id_cliente))


def denuncia_do_cliente(db: Session, id_avaliacao: int, id_cliente: uuid.UUID) -> DenunciaAvaliacao | None:
    return db.scalar(select(DenunciaAvaliacao).where(
        DenunciaAvaliacao.id_avaliacao == id_avaliacao, DenunciaAvaliacao.id_cliente == id_cliente))


def buscar_denuncia(db: Session, id_denuncia: int, travar: bool = False) -> DenunciaAvaliacao | None:
    consulta = select(DenunciaAvaliacao).where(DenunciaAvaliacao.id_denuncia == id_denuncia)
    if travar:
        consulta = consulta.with_for_update()
    return db.scalar(consulta.execution_options(populate_existing=True))


Denunciante = aliased(Usuario, name="denunciante")


def consulta_denuncias(id_denuncia=None, status=None) -> Select:
    d = DenunciaAvaliacao
    consulta = (
        select(*d.__table__.columns, Denunciante.nome.label("cliente"), Avaliacao.nota, Avaliacao.texto,
               Avaliacao.status.label("status_avaliacao"))
        .join(Avaliacao, Avaliacao.id_avaliacao == d.id_avaliacao)
        .join(Denunciante, Denunciante.id_usuario == d.id_cliente)
        .order_by(d.criada_em, d.id_denuncia)
    )
    if id_denuncia is not None:
        consulta = consulta.where(d.id_denuncia == id_denuncia)
    if status is not None:
        consulta = consulta.where(d.status == status)
    return consulta


def listar_denuncias(db: Session, limit: int, offset: int, status=None) -> tuple[list, int]:
    consulta = consulta_denuncias(status=status)
    total = db.scalar(select(func.count()).select_from(consulta.order_by(None).subquery()))
    return list(db.execute(consulta.limit(limit).offset(offset)).mappings().all()), total


def detalhar_denuncia(db: Session, id_denuncia: int):
    return db.execute(consulta_denuncias(id_denuncia=id_denuncia)).mappings().first()
