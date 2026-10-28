from collections import defaultdict
from datetime import date, datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.models.assistente import CodigoVerificacao, ConversaCanal, ResumoConversa
from src.models.catalogo import CategoriaProduto, Produto, Variante
from src.models.estoque import Estoque, Unidade
from src.models.fidelidade import LotePontos
from src.models.horarios import ExcecaoHorario, HorarioUnidade


# lojas que atendem o público; o CD fica de fora
def lojas_ativas(db: Session) -> list[Unidade]:
    return list(db.scalars(
        select(Unidade).where(Unidade.tipo == "loja", Unidade.ativo).order_by(Unidade.marca, Unidade.nome)
    ))


def horarios_das_unidades(db: Session, ids_unidade: list[int]) -> dict[int, list[HorarioUnidade]]:
    por_unidade = defaultdict(list)
    if ids_unidade:
        consulta = select(HorarioUnidade).where(HorarioUnidade.id_unidade.in_(ids_unidade))
        for horario in db.scalars(consulta):
            por_unidade[horario.id_unidade].append(horario)
    return por_unidade


def excecoes_do_dia(db: Session, ids_unidade: list[int], dia: date) -> dict[int, ExcecaoHorario]:
    if not ids_unidade:
        return {}
    consulta = select(ExcecaoHorario).where(ExcecaoHorario.id_unidade.in_(ids_unidade), ExcecaoHorario.data == dia)
    return {excecao.id_unidade: excecao for excecao in db.scalars(consulta)}


# disponível (estoque menos o reservado) de uma variante em cada unidade ativa e canal, só onde tem peça
def disponivel_por_unidade(db: Session, id_variante: int) -> list[tuple[Unidade, str, int]]:
    disponivel = Estoque.quantidade - Estoque.quantidade_reservada
    consulta = (
        select(Unidade, Estoque.canal, disponivel)
        .join(Unidade, Unidade.id_unidade == Estoque.id_unidade)
        .where(Estoque.id_variante == id_variante, Unidade.ativo, disponivel > 0)
        .order_by(Unidade.nome)
    )
    return [(unidade, canal, quantidade) for unidade, canal, quantidade in db.execute(consulta)]


# cores à venda na vitrine (produto, categoria e variante ativos), para o assistente buscar com as
# palavras do catálogo
def cores_a_venda(db: Session) -> list[str]:
    consulta = (
        select(Variante.cor).distinct()
        .join(Produto, Produto.id_produto == Variante.id_produto)
        .join(CategoriaProduto, CategoriaProduto.id_categoria == Produto.id_categoria)
        .where(Variante.ativo, Produto.ativo, CategoriaProduto.ativo)
        .order_by(Variante.cor)
    )
    return list(db.scalars(consulta))


# ---------- conversa e código por e-mail ----------

# a primeira mensagem cria a conversa; duas mensagens juntas não criam duas (ON CONFLICT)
def garantir_conversa(db: Session, canal: str, id_externo: str) -> ConversaCanal:
    db.execute(insert(ConversaCanal).values(canal=canal, id_externo=id_externo).on_conflict_do_nothing())
    return buscar_conversa(db, canal, id_externo, travar=True)


def buscar_conversa(db: Session, canal: str, id_externo: str, travar: bool = False) -> ConversaCanal | None:
    consulta = select(ConversaCanal).where(ConversaCanal.canal == canal, ConversaCanal.id_externo == id_externo)
    if travar:
        consulta = consulta.with_for_update()
    return db.scalar(consulta)


def codigos_desde(db: Session, canal: str, id_externo: str, desde: datetime) -> int:
    return db.scalar(select(func.count()).select_from(CodigoVerificacao).where(
        CodigoVerificacao.canal == canal, CodigoVerificacao.id_externo == id_externo,
        CodigoVerificacao.criado_em >= desde,
    ))


# travado: duas ferramentas da mesma resposta podem anotar assuntos ao mesmo tempo
def buscar_resumo(db: Session, id_resumo: int) -> ResumoConversa | None:
    return db.scalar(select(ResumoConversa).where(ResumoConversa.id_resumo == id_resumo).with_for_update())


# o código que ainda vale: o mais novo, nem usado nem esgotado, dentro dos 10 min
def codigo_aberto(db: Session, canal: str, id_externo: str, agora: datetime) -> CodigoVerificacao | None:
    return db.scalar(
        select(CodigoVerificacao)
        .where(CodigoVerificacao.canal == canal, CodigoVerificacao.id_externo == id_externo,
               CodigoVerificacao.encerrado_em.is_(None), CodigoVerificacao.expira_em > agora)
        .order_by(CodigoVerificacao.criado_em.desc(), CodigoVerificacao.id_codigo.desc())
        .limit(1)
        .with_for_update()
    )


# pedir um código novo invalida os anteriores da conversa
def encerrar_codigos(db: Session, canal: str, id_externo: str, agora: datetime) -> None:
    db.execute(
        update(CodigoVerificacao)
        .where(CodigoVerificacao.canal == canal, CodigoVerificacao.id_externo == id_externo,
               CodigoVerificacao.encerrado_em.is_(None))
        .values(encerrado_em=agora)
    )


# pontos de lotes ainda sem data de vencimento, por marca (o resumo só traz o próximo vencimento)
def pontos_sem_vencimento(db: Session, id_cliente, id_cliente_sem_conta) -> dict[str, int]:
    dono = LotePontos.id_cliente == id_cliente if id_cliente is not None \
        else LotePontos.id_cliente_sem_conta == id_cliente_sem_conta
    consulta = (
        select(LotePontos.marca, func.sum(LotePontos.restante))
        .where(dono, LotePontos.restante > 0, LotePontos.vence_em.is_(None))
        .group_by(LotePontos.marca)
    )
    return {marca: int(pontos) for marca, pontos in db.execute(consulta)}


def unidades(db: Session, ids_unidade: list[int]) -> dict[int, Unidade]:
    if not ids_unidade:
        return {}
    return {u.id_unidade: u for u in db.scalars(select(Unidade).where(Unidade.id_unidade.in_(ids_unidade)))}
