from datetime import datetime, timezone

from sqlalchemy.orm import Session

from src.models.atendimento import Chamado, HistoricoChamado, Mensagem
from src.models.contas import Usuario
from src.repositories import atendimento_repository as repo
from src.repositories import estoque_repository, permissao_repository, unidade_repository
from src.use_cases import arquivos
from src.use_cases.arquivos import ANEXO_CHAMADO
from src.use_cases.erros import Conflito, RecursoNaoEncontrado, RegraDeNegocio, SemPermissao, ServicoIndisponivel
from src.use_cases.permissoes import usuario_tem_permissao
from src.utils.supabase_storage import VALIDADE_DO_LINK

NAO_ENCONTRADO = "Chamado não encontrado"
CONCLUIDO = "Chamado concluído não muda mais: não há reabertura"


def _agora() -> datetime:
    return datetime.now(timezone.utc)


# toda mudança de status, responsável ou prioridade vai para o histórico, nunca editado
def _registrar(db: Session, chamado: Chamado, autor: Usuario, campo: str, anterior, novo) -> None:
    db.add(HistoricoChamado(
        id_chamado=chamado.id_chamado, id_autor=autor.id_usuario, campo_alterado=campo,
        valor_anterior=None if anterior is None else str(anterior), valor_novo=str(novo),
    ))


def _pagina(itens: list, total: int, limit: int, offset: int) -> dict:
    return {"items": itens, "total": total, "limit": limit, "offset": offset}


# ---------- lado do cliente ----------

def abrir_chamado(db: Session, cliente: Usuario, dados: dict) -> dict:
    id_pedido, id_item = dados.get("id_pedido"), dados.get("id_item_pedido")
    # pedido e chamado anterior só do próprio cliente; "não encontrado" não revela se existem
    if id_pedido is not None:
        pedido = repo.buscar_pedido(db, id_pedido)
        if pedido is None or pedido.id_cliente != cliente.id_usuario:
            raise RecursoNaoEncontrado("Pedido não encontrado")
    if id_item is not None:
        if id_pedido is None:
            raise RegraDeNegocio("Para apontar um item, informe também o pedido")
        item = repo.buscar_item_pedido(db, id_item)
        if item is None or item.id_pedido != id_pedido:
            raise RecursoNaoEncontrado("Item não encontrado nesse pedido")
    if dados.get("id_chamado_anterior") is not None:
        anterior = repo.buscar_chamado(db, dados["id_chamado_anterior"])
        if anterior is None or anterior.id_cliente != cliente.id_usuario:
            raise RecursoNaoEncontrado("Chamado anterior não encontrado")
    if dados.get("id_variante") is not None and estoque_repository.buscar_variante(db, dados["id_variante"]) is None:
        raise RecursoNaoEncontrado("Variante não encontrada")
    if dados.get("id_unidade") is not None and unidade_repository.buscar_unidade(db, dados["id_unidade"]) is None:
        raise RecursoNaoEncontrado("Unidade não encontrada")

    chamado = Chamado(**dados, id_cliente=cliente.id_usuario, status="aberto")
    db.add(chamado)
    db.flush()
    db.commit()
    return repo.detalhar_chamado(db, "cliente", chamado.id_chamado)


def _chamado_do_cliente(db: Session, cliente: Usuario, id_chamado: int) -> Chamado:
    chamado = repo.buscar_chamado(db, id_chamado)
    # chamado de outra pessoa responde como inexistente: trocar o número na URL não revela nada
    if chamado is None or chamado.id_cliente != cliente.id_usuario:
        raise RecursoNaoEncontrado(NAO_ENCONTRADO)
    return chamado


def listar_chamados_do_cliente(db: Session, cliente: Usuario, limit: int, offset: int, status=None) -> dict:
    itens, total = repo.listar_chamados(db, "cliente", limit, offset, id_cliente=cliente.id_usuario, status=status)
    return _pagina(itens, total, limit, offset)


def detalhar_chamado_do_cliente(db: Session, cliente: Usuario, id_chamado: int) -> dict:
    _chamado_do_cliente(db, cliente, id_chamado)
    return repo.detalhar_chamado(db, "cliente", id_chamado)


def mensagens_do_cliente(db: Session, cliente: Usuario, id_chamado: int) -> list:
    chamado = _chamado_do_cliente(db, cliente, id_chamado)
    repo.marcar_lidas(db, id_chamado, chamado.id_cliente, "cliente")
    db.commit()
    # mensagens internas da equipe nunca aparecem para o cliente
    return repo.listar_mensagens(db, id_chamado, incluir_internas=False)


def _nova_mensagem(db: Session, chamado: Chamado, autor: Usuario, conteudo: str | None, interna: bool,
                   anexo: dict | None = None) -> dict:
    if chamado.status == "concluido":
        raise RegraDeNegocio(CONCLUIDO)
    mensagem = Mensagem(id_chamado=chamado.id_chamado, id_autor=autor.id_usuario, conteudo=conteudo, interna=interna,
                        **(anexo or {}))
    db.add(mensagem)
    db.flush()
    db.refresh(mensagem)  # traz criado_em do banco
    db.commit()
    colunas = {c.key: getattr(mensagem, c.key) for c in Mensagem.__table__.columns}
    return {**colunas, "autor": autor.nome, "da_equipe": autor.id_usuario != chamado.id_cliente}


# o anexo vai para a área privada do Storage antes da gravação; se a mensagem não for gravada,
# o arquivo sai (case, seção 5: toda mensagem tem texto ou anexo)
def _mensagem_com_anexo(db: Session, storage, chamado: Chamado, autor: Usuario, arquivo, conteudo: str | None,
                        interna: bool) -> dict:
    if chamado.status == "concluido":
        raise RegraDeNegocio(CONCLUIDO)
    caminho = arquivos.enviar(storage, ANEXO_CHAMADO, f"chamado-{chamado.id_chamado}", arquivo)
    anexo = {"anexo_caminho": caminho, "anexo_nome": arquivo.nome[-255:], "anexo_tamanho": len(arquivo.conteudo)}
    try:
        return _nova_mensagem(db, chamado, autor, conteudo, interna, anexo)
    except Exception:
        db.rollback()
        arquivos.desfazer_envio(storage, ANEXO_CHAMADO, caminho)
        raise


# link temporário do anexo; a mensagem precisa ser do chamado (e, para o cliente, não ser interna)
def _link_do_anexo(db: Session, storage, chamado: Chamado, id_mensagem: int, incluir_internas: bool) -> dict:
    mensagem = repo.buscar_mensagem(db, id_mensagem)
    if (mensagem is None or mensagem.id_chamado != chamado.id_chamado or mensagem.anexo_caminho is None
            or (mensagem.interna and not incluir_internas)):
        raise RecursoNaoEncontrado("Anexo não encontrado")
    url = arquivos.url_temporaria(storage, ANEXO_CHAMADO, mensagem.anexo_caminho)
    if url is None:
        raise ServicoIndisponivel("Não foi possível abrir o anexo. Tente de novo")
    return {"url": url, "nome": mensagem.anexo_nome, "expira_em_segundos": VALIDADE_DO_LINK}


def enviar_mensagem_do_cliente(db: Session, cliente: Usuario, id_chamado: int, conteudo: str) -> dict:
    chamado = _chamado_do_cliente(db, cliente, id_chamado)
    return _nova_mensagem(db, chamado, cliente, conteudo, interna=False)


def anexar_do_cliente(db: Session, storage, cliente: Usuario, id_chamado: int, arquivo, conteudo: str | None) -> dict:
    chamado = _chamado_do_cliente(db, cliente, id_chamado)
    return _mensagem_com_anexo(db, storage, chamado, cliente, arquivo, conteudo, interna=False)


def anexo_para_o_cliente(db: Session, storage, cliente: Usuario, id_chamado: int, id_mensagem: int) -> dict:
    chamado = _chamado_do_cliente(db, cliente, id_chamado)
    return _link_do_anexo(db, storage, chamado, id_mensagem, incluir_internas=False)


# ---------- lado da equipe (permissão atender_chamado) ----------

def _chamado(db: Session, id_chamado: int, travar: bool = False) -> Chamado:
    chamado = repo.travar_chamado(db, id_chamado) if travar else repo.buscar_chamado(db, id_chamado)
    if chamado is None:
        raise RecursoNaoEncontrado(NAO_ENCONTRADO)
    return chamado


def listar_fila(db: Session, usuario: Usuario, limit: int, offset: int, meus: bool = False, **filtros) -> dict:
    if meus:
        filtros["id_responsavel"] = usuario.id_usuario
    itens, total = repo.listar_chamados(db, "equipe", limit, offset, **filtros)
    return _pagina(itens, total, limit, offset)


def detalhar_chamado(db: Session, id_chamado: int) -> dict:
    _chamado(db, id_chamado)
    return repo.detalhar_chamado(db, "equipe", id_chamado)


# quem tem permissão assume um chamado da fila; ninguém assume um chamado que já tem responsável
def assumir(db: Session, usuario: Usuario, id_chamado: int) -> dict:
    chamado = _chamado(db, id_chamado, travar=True)
    if chamado.status == "concluido":
        raise RegraDeNegocio(CONCLUIDO)
    if chamado.id_responsavel is not None:
        raise Conflito("Chamado já tem responsável")

    _registrar(db, chamado, usuario, "responsavel", None, usuario.id_usuario)
    _registrar(db, chamado, usuario, "status", chamado.status, "em_andamento")
    chamado.id_responsavel = usuario.id_usuario
    chamado.status = "em_andamento"
    chamado.assumido_em = _agora()
    db.commit()
    return repo.detalhar_chamado(db, "equipe", id_chamado)


def _exigir_responsavel(chamado: Chamado, usuario: Usuario) -> None:
    if chamado.status == "concluido":
        raise RegraDeNegocio(CONCLUIDO)
    if chamado.id_responsavel != usuario.id_usuario:
        raise SemPermissao("Só o responsável pelo chamado pode fazer isso")


# o responsável define a prioridade e pode repassar para outra pessoa que atende chamados
def alterar(db: Session, usuario: Usuario, id_chamado: int, campos: dict) -> dict:
    chamado = _chamado(db, id_chamado, travar=True)
    _exigir_responsavel(chamado, usuario)

    prioridade = campos.get("prioridade")
    if prioridade is not None and prioridade != chamado.prioridade:
        _registrar(db, chamado, usuario, "prioridade", chamado.prioridade, prioridade)
        chamado.prioridade = prioridade

    novo = campos.get("id_responsavel")
    if novo is not None and novo != chamado.id_responsavel:
        destino = permissao_repository.buscar_usuario(db, novo)
        if destino is None or destino.status_conta != "ativa" or not usuario_tem_permissao(db, destino, "atender_chamado"):
            raise RegraDeNegocio("O chamado só pode ser repassado para alguém ativo que atende chamados")
        _registrar(db, chamado, usuario, "responsavel", chamado.id_responsavel, novo)
        chamado.id_responsavel = novo

    db.commit()
    return repo.detalhar_chamado(db, "equipe", id_chamado)


def concluir(db: Session, usuario: Usuario, id_chamado: int, motivo: str) -> dict:
    chamado = _chamado(db, id_chamado, travar=True)
    _exigir_responsavel(chamado, usuario)

    _registrar(db, chamado, usuario, "status", chamado.status, "concluido")
    chamado.status = "concluido"
    chamado.motivo_encerramento = motivo
    chamado.concluido_em = _agora()
    db.commit()
    return repo.detalhar_chamado(db, "equipe", id_chamado)


def mensagens_da_equipe(db: Session, id_chamado: int) -> list:
    chamado = _chamado(db, id_chamado)
    repo.marcar_lidas(db, id_chamado, chamado.id_cliente, "equipe")
    db.commit()
    return repo.listar_mensagens(db, id_chamado, incluir_internas=True)


def enviar_mensagem_da_equipe(db: Session, usuario: Usuario, id_chamado: int, conteudo: str, interna: bool) -> dict:
    return _nova_mensagem(db, _chamado(db, id_chamado), usuario, conteudo, interna)


def anexar_da_equipe(db: Session, storage, usuario: Usuario, id_chamado: int, arquivo, conteudo: str | None,
                     interna: bool) -> dict:
    return _mensagem_com_anexo(db, storage, _chamado(db, id_chamado), usuario, arquivo, conteudo, interna)


def anexo_para_a_equipe(db: Session, storage, id_chamado: int, id_mensagem: int) -> dict:
    return _link_do_anexo(db, storage, _chamado(db, id_chamado), id_mensagem, incluir_internas=True)


def historico(db: Session, id_chamado: int) -> list:
    _chamado(db, id_chamado)
    return repo.listar_historico(db, id_chamado)
