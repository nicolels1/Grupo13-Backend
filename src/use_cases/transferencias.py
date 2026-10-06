import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from src.models.estoque import ItemTransferencia, Transferencia
from src.repositories import estoque_repository, unidade_repository
from src.repositories import transferencia_repository as repo
from src.use_cases.erros import RecursoNaoEncontrado, RegraDeNegocio
from src.use_cases.estoque import conferir_disponivel


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def montar(transferencia: Transferencia, itens: list[ItemTransferencia]) -> dict:
    dados = {coluna.key: getattr(transferencia, coluna.key) for coluna in Transferencia.__table__.columns}
    dados["itens"] = itens
    return dados


def _buscar(db: Session, id_transferencia: int, travar: bool = False) -> Transferencia:
    transferencia = repo.buscar_transferencia(db, id_transferencia, travar)
    if transferencia is None:
        raise RecursoNaoEncontrado("Transferência não encontrada")
    return transferencia


def _exigir_status(transferencia: Transferencia, status: str, acao: str) -> None:
    if transferencia.status != status:
        raise RegraDeNegocio(f"Só dá para {acao} uma transferência {status}; esta está {transferencia.status}")


def buscar(db: Session, id_transferencia: int) -> dict:
    transferencia = _buscar(db, id_transferencia)
    return montar(transferencia, repo.itens_da_transferencia(db, id_transferencia))


def listar(db: Session, status: str | None, id_unidade: int | None, limit: int, offset: int) -> dict:
    transferencias, total = repo.listar_transferencias(db, status, id_unidade, limit, offset)
    itens = repo.itens_das_transferencias(db, [t.id_transferencia for t in transferencias])
    lista = [montar(t, itens[t.id_transferencia]) for t in transferencias]
    return {"items": lista, "total": total, "limit": limit, "offset": offset}


def _unidade_ativa(db: Session, id_unidade: int, papel: str):
    unidade = unidade_repository.buscar_unidade(db, id_unidade)
    if unidade is None:
        raise RecursoNaoEncontrado(f"Unidade de {papel} não encontrada")
    if not unidade.ativo:
        raise RegraDeNegocio(f"Unidade de {papel} desativada")
    return unidade


# a solicitação não reserva estoque na origem; só registra o pedido
def solicitar(db: Session, id_usuario: uuid.UUID, dados: dict) -> dict:
    origem = _unidade_ativa(db, dados["id_unidade_origem"], "origem")
    destino = _unidade_ativa(db, dados["id_unidade_destino"], "destino")
    for item in dados["itens"]:
        if estoque_repository.buscar_variante(db, item["id_variante"]) is None:
            raise RecursoNaoEncontrado(f"Variante {item['id_variante']} não encontrada")
        if origem.tipo == "cd" and item["canal_saida"] != "online":
            raise RegraDeNegocio("Item que sai do CD sai do canal online")
        if destino.tipo == "cd" and item["canal_entrada"] != "online":
            raise RegraDeNegocio("O CD só recebe no canal online")

    transferencia = Transferencia(
        id_unidade_origem=origem.id_unidade, id_unidade_destino=destino.id_unidade,
        status="solicitada", id_solicitante=id_usuario,
    )
    db.add(transferencia)
    db.flush()
    itens = [
        ItemTransferencia(
            id_transferencia=transferencia.id_transferencia, id_variante=item["id_variante"],
            canal_saida=item["canal_saida"], canal_entrada=item["canal_entrada"],
            quantidade_solicitada=item["quantidade"],
        )
        for item in dados["itens"]
    ]
    db.add_all(itens)
    db.commit()
    return buscar(db, transferencia.id_transferencia)


# {id_item: quantidade} informado; itens fora da lista usam o padrão
def _quantidades(itens: list[ItemTransferencia], informadas: list[dict], padrao) -> dict[int, int]:
    por_item = {i.id_item_transferencia: i for i in itens}
    resultado = {i.id_item_transferencia: padrao(i) for i in itens}
    for informada in informadas:
        if informada["id_item_transferencia"] not in por_item:
            raise RegraDeNegocio(f"Item {informada['id_item_transferencia']} não é desta transferência")
        resultado[informada["id_item_transferencia"]] = informada["quantidade"]
    return resultado


# envio: cada item sai do disponível da origem (pode sair menos que o solicitado).
# O disponível é conferido pelo total de cada variante e canal, com as linhas de ESTOQUE
# travadas sempre na mesma ordem (como no estoque, para duas operações não se travarem)
def enviar(db: Session, id_transferencia: int, id_usuario: uuid.UUID, dados: dict) -> dict:
    transferencia = _buscar(db, id_transferencia, travar=True)
    _exigir_status(transferencia, "solicitada", "enviar")
    itens = repo.itens_da_transferencia(db, id_transferencia)
    enviadas = _quantidades(itens, dados.get("itens", []), lambda i: i.quantidade_solicitada)
    if sum(enviadas.values()) == 0:
        raise RegraDeNegocio("Nenhuma peça enviada; para desistir, cancele a transferência")

    origem = transferencia.id_unidade_origem
    por_variante: dict[tuple[int, str], int] = {}
    for item in itens:
        chave = (item.id_variante, item.canal_saida)
        por_variante[chave] = por_variante.get(chave, 0) + enviadas[item.id_item_transferencia]
    for (id_variante, canal), total in sorted(por_variante.items()):
        if total:
            linha = estoque_repository.travar_estoque(db, id_variante, origem, [canal]).get(canal)
            conferir_disponivel(linha, total)

    for item in itens:
        quantidade = enviadas[item.id_item_transferencia]
        item.quantidade_enviada = quantidade
        if quantidade:
            estoque_repository.inserir_movimentacao(
                db, id_variante=item.id_variante, id_unidade=origem, canal=item.canal_saida,
                tipo="saida_transferencia", quantidade=-quantidade,
                id_transferencia=id_transferencia, id_usuario=id_usuario,
            )

    transferencia.status, transferencia.enviada_em, transferencia.id_enviado_por = "enviada", _agora(), id_usuario
    db.commit()
    return buscar(db, id_transferencia)


# recebimento: entra o total enviado no destino e, se chegou menos, a diferença sai como perda ou avaria
def receber(db: Session, id_transferencia: int, id_usuario: uuid.UUID, dados: dict) -> dict:
    transferencia = _buscar(db, id_transferencia, travar=True)
    _exigir_status(transferencia, "enviada", "receber")
    itens = repo.itens_da_transferencia(db, id_transferencia)
    recebidas = _quantidades(itens, dados.get("itens", []), lambda i: i.quantidade_enviada or 0)

    diferencas = {i.id_item_transferencia: (i.quantidade_enviada or 0) - recebidas[i.id_item_transferencia] for i in itens}
    if any(d < 0 for d in diferencas.values()):
        raise RegraDeNegocio("Quantidade recebida maior que a enviada")
    if any(diferencas.values()) and not dados.get("motivo_diferenca"):
        raise RegraDeNegocio("Chegou menos do que foi enviado: informe o motivo da diferença")

    destino = transferencia.id_unidade_destino
    for item in itens:
        enviada = item.quantidade_enviada or 0
        item.quantidade_recebida = recebidas[item.id_item_transferencia]
        comum = dict(id_variante=item.id_variante, id_unidade=destino, canal=item.canal_entrada,
                     id_transferencia=id_transferencia, id_usuario=id_usuario)
        if enviada:
            estoque_repository.inserir_movimentacao(db, tipo="entrada_transferencia", quantidade=enviada, **comum)
        if diferencas[item.id_item_transferencia]:
            estoque_repository.inserir_movimentacao(
                db, tipo=dados.get("tipo_diferenca", "perda"), quantidade=-diferencas[item.id_item_transferencia],
                motivo=dados["motivo_diferenca"], **comum,
            )

    transferencia.status, transferencia.recebida_em, transferencia.id_recebido_por = "recebida", _agora(), id_usuario
    db.commit()
    return buscar(db, id_transferencia)


# antes do envio, origem ou destino cancelam com motivo; depois de enviada, não se cancela
def cancelar(db: Session, id_transferencia: int, id_usuario: uuid.UUID, motivo: str) -> dict:
    transferencia = _buscar(db, id_transferencia, travar=True)
    _exigir_status(transferencia, "solicitada", "cancelar")
    transferencia.status = "cancelada"
    transferencia.motivo_cancelamento = motivo
    transferencia.id_cancelado_por = id_usuario
    transferencia.cancelada_em = _agora()
    db.commit()
    return buscar(db, id_transferencia)
