import uuid

from sqlalchemy import Select, String, and_, case, cast, func, or_, select, update
from sqlalchemy.orm import Session, aliased

from src.models.atendimento import Chamado, HistoricoChamado, Mensagem
from src.models.catalogo import Produto, Variante
from src.models.contas import ClienteSemConta, Usuario
from src.models.estoque import Unidade
from src.models.migracao import OrigemMigracao
from src.models.vendas import ItemPedido, Pedido

Cliente = aliased(Usuario, name="cliente")
Responsavel = aliased(Usuario, name="responsavel")


# o chamado é da marca pela unidade, pelo pedido ou pela peça ligados a ele; o migrado do atendimento da Vulto
# é da Vulto mesmo sem nenhum deles. O novo sem ligação (dúvida geral) não tem marca e só entra na visão das duas
def da_marca(marca: str):
    unidades = select(Unidade.id_unidade).where(Unidade.marca == marca)
    condicoes = [
        Chamado.id_unidade.in_(unidades),
        Chamado.id_pedido.in_(select(Pedido.id_pedido).where(Pedido.id_unidade.in_(unidades))),
        Chamado.id_variante.in_(select(Variante.id_variante)
                                .join(Produto, Produto.id_produto == Variante.id_produto)
                                .where(Produto.marca == marca)),
    ]
    if marca == "vulto":
        condicoes.append(cast(Chamado.id_chamado, String).in_(
            select(OrigemMigracao.id_registro).where(OrigemMigracao.tabela == "chamado")))
    return or_(*condicoes)


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


# fala do cliente do chamado: sem autor (a do cliente no atendimento migrado da Vulto, P6) ou escrita pela
# conta dele. IS NOT DISTINCT FROM: no chamado de cliente sem conta (id_cliente vazio), toda fala com autor é
# da equipe, e a fala sem autor continua do cliente depois que ele cria a conta
def _do_cliente(id_cliente):
    return or_(Mensagem.id_autor.is_(None), Mensagem.id_autor.is_not_distinct_from(id_cliente))


# mensagens do outro lado ainda não lidas: para a equipe, as do cliente; para o
# cliente, as da equipe que não são internas
def _nao_lidas_do_outro_lado(lado: str):
    m = Mensagem
    do_cliente = _do_cliente(Chamado.id_cliente)
    condicao = do_cliente if lado == "equipe" else and_(~do_cliente, m.interna.is_(False))
    return (
        select(func.count())
        .where(m.id_chamado == Chamado.id_chamado, m.lida_em.is_(None), condicao)
        .correlate(Chamado)
        .scalar_subquery()
    )


ORDEM_DA_PRIORIDADE = {"alta": 0, "media": 1, "baixa": 2}
# a definida por quem assumiu vale sobre a sugerida pela IA
prioridade_que_vale = func.coalesce(Chamado.prioridade, Chamado.prioridade_sugerida)


# o cliente vê os mais novos primeiro. A fila da equipe põe os abertos antes dos concluídos e, entre os abertos, a
# prioridade (a definida por quem assumiu ou, sem ela, a sugerida pela IA) e depois quem espera há mais tempo;
# os concluídos seguem dos mais novos para os mais antigos
def _ordem(lado: str) -> list:
    if lado != "equipe":
        return [Chamado.criado_em.desc(), Chamado.id_chamado.desc()]
    concluido = Chamado.status == "concluido"
    prioridade = case(ORDEM_DA_PRIORIDADE, value=prioridade_que_vale,
                      else_=len(ORDEM_DA_PRIORIDADE))
    return [
        case((concluido, 1), else_=0),
        case((concluido, 0), else_=prioridade),
        case((~concluido, Chamado.criado_em)).asc(),
        Chamado.criado_em.desc(), Chamado.id_chamado.desc(),
    ]


def consulta_chamados(
    lado: str, id_chamado=None, id_cliente=None, id_cliente_sem_conta=None, status=None, categoria=None,
    prioridade=None, id_unidade=None, id_responsavel=None, sem_responsavel=None, com_mensagem_nova=None,
    marca=None,
) -> Select:
    nao_lidas = _nao_lidas_do_outro_lado(lado)
    # cliente com conta ou cliente sem conta da migração (P2); este sem nome aparece pelo e-mail. O migrado
    # sem cliente (A5–A7) aparece pelo contato externo
    cliente = func.coalesce(Cliente.nome, ClienteSemConta.nome, ClienteSemConta.email, Chamado.contato_externo)
    consulta = (
        select(
            *Chamado.__table__.columns, cliente.label("cliente"), Responsavel.nome.label("responsavel"),
            nao_lidas.label("mensagens_nao_lidas"),
        )
        .outerjoin(Cliente, Cliente.id_usuario == Chamado.id_cliente)
        .outerjoin(ClienteSemConta, ClienteSemConta.id_cliente_sem_conta == Chamado.id_cliente_sem_conta)
        .outerjoin(Responsavel, Responsavel.id_usuario == Chamado.id_responsavel)
        .order_by(*_ordem(lado))
    )
    filtros = []
    for coluna, valor in [
        (Chamado.id_chamado, id_chamado), (Chamado.id_cliente, id_cliente),
        (Chamado.id_cliente_sem_conta, id_cliente_sem_conta), (Chamado.status, status),
        (Chamado.categoria, categoria), (Chamado.id_unidade, id_unidade), (Chamado.id_responsavel, id_responsavel),
    ]:
        if valor is not None:
            filtros.append(coluna == valor)
    # a prioridade que vale: a definida por quem assumiu ou, sem ela, a sugerida pela IA ("sem" = nenhuma das duas)
    if prioridade is not None:
        filtros.append(prioridade_que_vale.is_(None) if prioridade == "sem" else prioridade_que_vale == prioridade)
    if sem_responsavel is not None:
        filtros.append(Chamado.id_responsavel.is_(None) if sem_responsavel else Chamado.id_responsavel.is_not(None))
    if com_mensagem_nova is not None:
        filtros.append(nao_lidas > 0 if com_mensagem_nova else nao_lidas == 0)
    if marca is not None:
        filtros.append(da_marca(marca))
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
        # no atendimento migrado, o nome de quem falou: o cliente sem autor e o original nas falas da "Migração Vulto"
        select(
            *m.__table__.columns, func.coalesce(m.nome_autor_externo, Usuario.nome).label("autor"),
            ~_do_cliente(Chamado.id_cliente).label("da_equipe"),
        )
        .join(Chamado, Chamado.id_chamado == m.id_chamado)
        .outerjoin(Usuario, Usuario.id_usuario == m.id_autor)
        .where(m.id_chamado == id_chamado)
        .order_by(m.criado_em, m.id_mensagem)
    )
    return consulta if incluir_internas else consulta.where(m.interna.is_(False))


def listar_mensagens(db: Session, id_chamado: int, incluir_internas: bool) -> list:
    return list(db.execute(consulta_mensagens(id_chamado, incluir_internas)).mappings().all())


# quem abre as mensagens marca como lidas as do outro lado (a Visão Geral conta as não lidas)
def comando_marcar_lidas(id_chamado: int, id_cliente: uuid.UUID | None, lado: str):
    m = Mensagem
    do_cliente = _do_cliente(id_cliente)
    condicao = do_cliente if lado == "equipe" else and_(~do_cliente, m.interna.is_(False))
    return (
        update(m)
        .where(m.id_chamado == id_chamado, m.lida_em.is_(None), condicao)
        .values(lida_em=func.now())
    )


def marcar_lidas(db: Session, id_chamado: int, id_cliente: uuid.UUID | None, lado: str) -> None:
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
