from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from src.models.contas import Usuario
from src.models.estoque import Estoque, MovimentacaoEstoque
from src.repositories import estoque_repository as repo
from src.use_cases.erros import RecursoNaoEncontrado, RegraDeNegocio

BRASILIA = ZoneInfo("America/Sao_Paulo")
CANAIS = ("loja_fisica", "online")
TIPOS_COM_MOTIVO = {"avaria", "perda", "ajuste"}
TIPOS_DE_SAIDA = {"avaria", "perda"}
LIMITE_DE_PONTOS = 1000


# ---------- regras de entrada ----------

# recebimento, avaria e perda chegam como número de peças; o sinal vem do tipo.
# ajuste já vem com sinal (+ entra, - sai)
def quantidade_com_sinal(tipo: str, quantidade: int) -> int:
    if quantidade == 0:
        raise RegraDeNegocio("A quantidade não pode ser zero")
    if tipo == "ajuste":
        return quantidade
    if quantidade < 0:
        raise RegraDeNegocio("Informe o número de peças (positivo): a entrada ou saída vem do tipo")
    return -quantidade if tipo in TIPOS_DE_SAIDA else quantidade


def _validar_local(db: Session, id_variante: int, id_unidade: int, canal: str):
    variante = repo.buscar_variante(db, id_variante)
    if variante is None:
        raise RecursoNaoEncontrado("Variante não encontrada")
    unidade = repo.buscar_unidade(db, id_unidade)
    if unidade is None:
        raise RecursoNaoEncontrado("Unidade não encontrada")
    if not unidade.ativo:
        raise RegraDeNegocio("Unidade desativada")
    if unidade.tipo == "cd" and canal == "loja_fisica":
        raise RegraDeNegocio("O CD só tem estoque online")
    return variante, unidade


# saída só usa o disponível: estoque menos o reservado para pedidos online (case, seção 5)
def _conferir_disponivel(linha: Estoque | None, saida: int) -> None:
    disponivel = linha.quantidade - linha.quantidade_reservada if linha else 0
    if saida > disponivel:
        raise RegraDeNegocio(f"Estoque disponível insuficiente: há {disponivel} peça(s) disponível(is)")


# ---------- escritas ----------

def registrar_movimentacao(
    db: Session, usuario: Usuario, id_variante: int, id_unidade: int, canal: str, tipo: str,
    quantidade: int, motivo: str | None,
) -> MovimentacaoEstoque:
    motivo = (motivo or "").strip() or None
    if tipo in TIPOS_COM_MOTIVO and motivo is None:
        raise RegraDeNegocio("Motivo obrigatório em avaria, perda e ajuste")
    quantidade = quantidade_com_sinal(tipo, quantidade)
    variante, _ = _validar_local(db, id_variante, id_unidade, canal)
    if tipo == "recebimento" and not variante.ativo:
        raise RegraDeNegocio("Variante desativada não recebe mercadoria")

    if quantidade < 0:
        linha = repo.travar_estoque(db, id_variante, id_unidade, [canal]).get(canal)
        _conferir_disponivel(linha, -quantidade)

    movimentacao = repo.inserir_movimentacao(
        db, id_variante=id_variante, id_unidade=id_unidade, canal=canal, id_usuario=usuario.id_usuario,
        tipo=tipo, quantidade=quantidade, motivo=motivo,
    )
    db.commit()
    return movimentacao


# passa peças disponíveis de um canal para o outro na mesma loja: saída e entrada juntas
def realocar(
    db: Session, usuario: Usuario, id_variante: int, id_unidade: int, canal_origem: str, quantidade: int
) -> list[MovimentacaoEstoque]:
    if quantidade <= 0:
        raise RegraDeNegocio("A quantidade precisa ser positiva")
    canal_destino = "online" if canal_origem == "loja_fisica" else "loja_fisica"
    _, unidade = _validar_local(db, id_variante, id_unidade, "online")
    if unidade.tipo == "cd":
        raise RegraDeNegocio("O CD só tem estoque online: não há realocação entre canais")

    linhas = repo.travar_estoque(db, id_variante, id_unidade, sorted(CANAIS))
    _conferir_disponivel(linhas.get(canal_origem), quantidade)

    comuns = dict(id_variante=id_variante, id_unidade=id_unidade, id_usuario=usuario.id_usuario)
    saida = repo.inserir_movimentacao(db, **comuns, canal=canal_origem, tipo="saida_realocacao",
                                      quantidade=-quantidade)
    entrada = repo.inserir_movimentacao(db, **comuns, canal=canal_destino, tipo="entrada_realocacao",
                                        quantidade=quantidade)
    db.commit()
    return [saida, entrada]


# o mínimo não é saldo: pode ser definido mesmo antes da primeira movimentação
def definir_minimo(
    db: Session, usuario: Usuario, id_variante: int, id_unidade: int, canal: str, estoque_minimo: int | None,
) -> dict:
    _validar_local(db, id_variante, id_unidade, canal)
    linha = repo.buscar_estoque(db, id_variante, id_unidade, canal)
    if linha is None:
        linha = Estoque(id_variante=id_variante, id_unidade=id_unidade, canal=canal)
        repo.adicionar_estoque(db, linha)

    linha.estoque_minimo = estoque_minimo
    linha.minimo_alterado_por = usuario.id_usuario
    linha.minimo_alterado_em = datetime.now(timezone.utc)
    db.commit()

    itens, _ = repo.listar_estoque(db, 1, 0, id_unidade=id_unidade, id_variante=id_variante, canal=canal)
    return itens[0]


# ---------- leituras ----------

def _pagina(itens: list, total: int, limit: int, offset: int) -> dict:
    return {"items": itens, "total": total, "limit": limit, "offset": offset}


def listar_estoque(db: Session, limit: int, offset: int, **filtros) -> dict:
    return _pagina(*repo.listar_estoque(db, limit, offset, **filtros), limit, offset)


def listar_movimentacoes(
    db: Session, limit: int, offset: int, de: str | None = None, ate: str | None = None, **filtros
) -> dict:
    filtros["de"] = interpretar_momento(de, fim_do_dia=False) if de else None
    filtros["ate"] = interpretar_momento(ate) if ate else None
    return _pagina(*repo.listar_movimentacoes(db, limit, offset, **filtros), limit, offset)


def listar_divergencias(db: Session) -> list:
    return repo.listar_divergencias(db)


# data sem hora vale o fim do dia (ou o começo, no início de um intervalo) no horário de
# Brasília; data e hora sem fuso também são de Brasília (case, seção 5)
def interpretar_momento(texto: str, fim_do_dia: bool = True) -> datetime:
    try:
        if len(texto) == 10:
            dia = date.fromisoformat(texto)
            return datetime.combine(dia, time.max if fim_do_dia else time.min, BRASILIA)
        momento = datetime.fromisoformat(texto)
    except ValueError:
        raise RegraDeNegocio("Data inválida: use AAAA-MM-DD ou AAAA-MM-DDTHH:MM")
    return momento if momento.tzinfo else momento.replace(tzinfo=BRASILIA)


# "ver estoque em": quanto havia em cada variante, unidade e canal naquele momento
def estoque_em(db: Session, em: str, limit: int, offset: int, **filtros) -> dict:
    momento = interpretar_momento(em)
    return _pagina(*repo.listar_historico(db, momento, limit, offset, **filtros), limit, offset)


# granularidade automática do gráfico: até 2 dias por hora, até 90 dias por dia, acima por semana
def escolher_granularidade(inicio: datetime, fim: datetime) -> str:
    duracao = fim - inicio
    if duracao <= timedelta(days=2):
        return "hora"
    if duracao <= timedelta(days=90):
        return "dia"
    return "semana"


def inicio_do_periodo(momento: datetime, granularidade: str) -> datetime:
    """Começo da hora, do dia ou da semana (segunda-feira), como o date_trunc do PostgreSQL."""
    if granularidade == "hora":
        return momento.replace(minute=0, second=0, microsecond=0)
    dia = momento.replace(hour=0, minute=0, second=0, microsecond=0)
    return dia if granularidade == "dia" else dia - timedelta(days=dia.weekday())


def periodos(inicio: datetime, fim: datetime, granularidade: str) -> list[datetime]:
    """Começo de cada período entre inicio e fim, no horário de Brasília e sem fuso
    (no mesmo formato que o banco devolve)."""
    passo = {"hora": timedelta(hours=1), "dia": timedelta(days=1), "semana": timedelta(weeks=1)}[granularidade]
    atual = inicio_do_periodo(inicio.astimezone(BRASILIA).replace(tzinfo=None), granularidade)
    ultimo = fim.astimezone(BRASILIA).replace(tzinfo=None)
    lista = []
    while atual <= ultimo:
        lista.append(atual)
        if len(lista) > LIMITE_DE_PONTOS:
            raise RegraDeNegocio("Período longo demais para essa granularidade: escolha uma maior")
        atual += passo
    return lista


# gráfico de evolução com uma linha por canal; cada ponto é o saldo no fim do período
def evolucao(
    db: Session, id_variante: int, id_unidade: int | None, inicio: str | None, fim: str | None,
    granularidade: str | None,
) -> dict:
    if repo.buscar_variante(db, id_variante) is None:
        raise RecursoNaoEncontrado("Variante não encontrada")
    if id_unidade is not None and repo.buscar_unidade(db, id_unidade) is None:
        raise RecursoNaoEncontrado("Unidade não encontrada")

    momento_fim = interpretar_momento(fim) if fim else datetime.now(timezone.utc)
    momento_inicio = interpretar_momento(inicio, fim_do_dia=False) if inicio else momento_fim - timedelta(days=30)
    if momento_inicio >= momento_fim:
        raise RegraDeNegocio("O início precisa ser antes do fim")
    granularidade = granularidade or escolher_granularidade(momento_inicio, momento_fim)
    lista = periodos(momento_inicio, momento_fim, granularidade)

    # o primeiro período começa no início da hora/dia/semana: o saldo de partida vai até ali
    inicio_consulta = lista[0].replace(tzinfo=BRASILIA)
    atual = {canal: 0 for canal in CANAIS}
    atual.update(repo.saldo_antes(db, id_variante, id_unidade, inicio_consulta))
    somas = defaultdict(dict)
    for periodo, canal, soma in repo.somas_por_periodo(
        db, id_variante, id_unidade, inicio_consulta, momento_fim, granularidade
    ):
        somas[periodo][canal] = soma

    pontos = []
    for periodo in lista:
        for canal, soma in somas.get(periodo, {}).items():
            atual[canal] += soma
        pontos.append({"inicio_periodo": periodo.replace(tzinfo=BRASILIA), **atual})

    return {"id_variante": id_variante, "id_unidade": id_unidade, "granularidade": granularidade, "pontos": pontos}
