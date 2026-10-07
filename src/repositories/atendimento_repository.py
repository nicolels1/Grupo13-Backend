import uuid

from sqlalchemy import Select, and_, func, select, update
from sqlalchemy.orm import Session, aliased

from src.models.atendimento import Chamado, HistoricoChamado, Mensagem
from src.models.contas import Usuario
from src.models.vendas import ItemPedido, Pedido

Cliente = aliased(Usuario, name="cliente")
Responsavel = aliased(Usuario, name="responsavel")


def buscar_chamado(db: Session, id_chamado: int) -> Chamado | None:
    return db.get(Chamado, id_chamado)


# trava o chamado: duas pessoas não assumem nem alteram o mesmo chamado ao mesmo tempo
def travar_chamado(db: Session, id_chamado: int) -> Chamado | None:
    return db.scalar(select(Chamado).where(Chamado.id_chamado == id_chamado).with_for_update())


def buscar_mensagem(db: Session, id_mensagem: int) -> Mensagem | None:
    return db.get(Mensagem, id_mensagem)


def buscar_pedido(db: Session, id_pedido: int) -> Pedido | None:
    return db.get(Pedido, id_pedido)


def buscar_item_pedido(db: Session, id_item: int) -> ItemPedido | None:
    return db.get(ItemPedido, id_item)


# mensagens do outro lado ainda não lidas: para a equipe, as do cliente; para o
# cliente, as da equipe que não são internas
def _nao_lidas_do_outro_lado(lado: str):
    m = Mensagem
    do_cliente = m.id_autor == Chamado.id_cliente
    condicao = do_cliente if lado == "equipe" else and_(~do_cliente, m.interna.is_(False))
    return (
        select(func.count())
        .where(m.id_chamado == Chamado.id_chamado, m.lida_em.is_(None), condicao)
        .correlate(Chamado)
        .scalar_subquery()
    )


def consulta_chamados(
    lado: str, id_chamado=None, id_cliente=None, status=None, categoria=None, prioridade=None,
    id_unidade=None, id_responsavel=None, sem_responsavel=None, com_mensagem_nova=None,
) -> Select:
    nao_lidas = _nao_lidas_do_outro_lado(lado)
    consulta = (
        select(
            *Chamado.__table__.columns, Cliente.nome.label("cliente"), Responsavel.nome.label("responsavel"),
            nao_lidas.label("mensagens_nao_lidas"),
        )
        .join(Cliente, Cliente.id_usuario == Chamado.id_cliente)
        .outerjoin(Responsavel, Responsavel.id_usuario == Chamado.id_responsavel)
        .order_by(Chamado.criado_em.desc(), Chamado.id_chamado.desc())
    )
    filtros = []
    for coluna, valor in [
        (Chamado.id_chamado, id_chamado), (Chamado.id_cliente, id_cliente), (Chamado.status, status),
        (Chamado.categoria, categoria), (Chamado.prioridade, prioridade), (Chamado.id_unidade, id_unidade),
        (Chamado.id_responsavel, id_responsavel),
    ]:
        if valor is not None:
            filtros.append(coluna == valor)
    if sem_responsavel is not None:
        filtros.append(Chamado.id_responsavel.is_(None) if sem_responsavel else Chamado.id_responsavel.is_not(None))
    if com_mensagem_nova is not None:
        filtros.append(nao_lidas > 0 if com_mensagem_nova else nao_lidas == 0)
    return consulta.where(*filtros)


def listar_chamados(db: Session, lado: str, limit: int, offset: int, **filtros) -> tuple[list, int]:
    consulta = consulta_chamados(lado, **filtros)
    total = db.scalar(select(func.count()).select_from(consulta.order_by(None).subquery()))
    linhas = db.execute(consulta.limit(limit).offset(offset)).mappings().all()
    return list(linhas), total


def detalhar_chamado(db: Session, lado: str, id_chamado: int):
    return db.execute(consulta_chamados(lado, id_chamado=id_chamado)).mappings().first()


def consulta_mensagens(id_chamado: int, incluir_internas: bool) -> Select:
    m = Mensagem
    consulta = (
        select(*m.__table__.columns, Usuario.nome.label("autor"), (m.id_autor != Chamado.id_cliente).label("da_equipe"))
        .join(Chamado, Chamado.id_chamado == m.id_chamado)
        .join(Usuario, Usuario.id_usuario == m.id_autor)
        .where(m.id_chamado == id_chamado)
        .order_by(m.criado_em, m.id_mensagem)
    )
    return consulta if incluir_internas else consulta.where(m.interna.is_(False))


def listar_mensagens(db: Session, id_chamado: int, incluir_internas: bool) -> list:
    return list(db.execute(consulta_mensagens(id_chamado, incluir_internas)).mappings().all())


# quem abre as mensagens marca como lidas as do outro lado (a Visão Geral conta as não lidas)
def comando_marcar_lidas(id_chamado: int, id_cliente: uuid.UUID, lado: str):
    m = Mensagem
    do_cliente = m.id_autor == id_cliente
    condicao = do_cliente if lado == "equipe" else and_(~do_cliente, m.interna.is_(False))
    return (
        update(m)
        .where(m.id_chamado == id_chamado, m.lida_em.is_(None), condicao)
        .values(lida_em=func.now())
    )


def marcar_lidas(db: Session, id_chamado: int, id_cliente: uuid.UUID, lado: str) -> None:
    db.execute(comando_marcar_lidas(id_chamado, id_cliente, lado))


def consulta_historico(id_chamado: int) -> Select:
    h = HistoricoChamado
    return (
        select(*h.__table__.columns, Usuario.nome.label("autor"))
        .join(Usuario, Usuario.id_usuario == h.id_autor)
        .where(h.id_chamado == id_chamado)
        .order_by(h.criado_em, h.id_historico)
    )


def listar_historico(db: Session, id_chamado: int) -> list:
    return list(db.execute(consulta_historico(id_chamado)).mappings().all())


# contas internas ativas, por nome: o use case filtra quem atende chamados (repassar)
def contas_internas_ativas(db: Session) -> list[Usuario]:
    consulta = (
        select(Usuario)
        .where(Usuario.tipo_conta == "interna", Usuario.status_conta == "ativa")
        .order_by(Usuario.nome)
    )
    return list(db.scalars(consulta).all())
