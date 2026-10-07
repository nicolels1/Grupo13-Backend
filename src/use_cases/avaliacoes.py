from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from src.models.avaliacoes import Avaliacao, DenunciaAvaliacao, FotoAvaliacao, VotoUtil
from src.models.contas import Usuario
from src.repositories import avaliacao_repository as repo
from src.use_cases import arquivos
from src.use_cases.arquivos import FOTO_AVALIACAO
from src.use_cases.erros import Conflito, RecursoNaoEncontrado, RegraDeNegocio
from src.use_cases.permissoes import usuario_tem_permissao

PRAZO_DE_EDICAO = timedelta(days=7)
MAXIMO_DE_FOTOS = 5
NAO_ENCONTRADA = "Avaliação não encontrada"


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _pagina(itens: list, total: int, limit: int, offset: int) -> dict:
    return {"items": itens, "total": total, "limit": limit, "offset": offset}


# fotos com link temporário (área privada do Storage); avaliação oculta não mostra fotos fora da moderação
def _montar(db: Session, storage, linhas: list, moderacao: bool = False) -> list[dict]:
    fotos = repo.fotos_das_avaliacoes(db, [linha["id_avaliacao"] for linha in linhas])
    resultado = []
    for linha in linhas:
        dados = dict(linha)
        dados["autor"] = (linha["autor"] or "").split(" ")[0]
        visiveis = fotos[linha["id_avaliacao"]] if moderacao or linha["status"] == "publicada" else []
        dados["fotos"] = [{"id_foto": f.id_foto, "ordem": f.ordem,
                           "url": arquivos.url_temporaria(storage, FOTO_AVALIACAO, f.caminho_arquivo)} for f in visiveis]
        if not moderacao:
            dados.update(motivo_ocultacao=None, ocultada_em=None, denuncias_pendentes=None)
        resultado.append(dados)
    return resultado


def _detalhar(db: Session, storage, id_avaliacao: int, moderacao: bool = False) -> dict:
    return _montar(db, storage, [repo.detalhar_avaliacao(db, id_avaliacao)], moderacao)[0]


def _avaliacao(db: Session, id_avaliacao: int, travar: bool = False) -> Avaliacao:
    avaliacao = repo.buscar_avaliacao(db, id_avaliacao, travar)
    if avaliacao is None:
        raise RecursoNaoEncontrado(NAO_ENCONTRADA)
    return avaliacao


# avaliação do próprio cliente, ainda dentro dos 7 dias de edição
def _minha_para_editar(db: Session, cliente: Usuario, id_avaliacao: int) -> Avaliacao:
    avaliacao = _avaliacao(db, id_avaliacao, travar=True)
    if repo.autor_da_avaliacao(db, id_avaliacao) != cliente.id_usuario:
        raise RecursoNaoEncontrado(NAO_ENCONTRADA)
    if avaliacao.criada_em + PRAZO_DE_EDICAO < _agora():
        raise RegraDeNegocio("A avaliação só pode ser editada nos 7 dias após a publicação")
    return avaliacao


# avaliação de outra pessoa, publicada: só ela recebe voto e denúncia
def _de_outra_pessoa(db: Session, cliente: Usuario, id_avaliacao: int) -> Avaliacao:
    avaliacao = _avaliacao(db, id_avaliacao)
    if avaliacao.status != "publicada":
        raise RecursoNaoEncontrado(NAO_ENCONTRADA)
    if repo.autor_da_avaliacao(db, id_avaliacao) == cliente.id_usuario:
        raise RegraDeNegocio("Ninguém vota nem denuncia a própria avaliação")
    return avaliacao


# ---------- leitura ----------

def do_produto(db: Session, storage, id_produto: int, limit: int, offset: int) -> dict:
    linhas, total = repo.listar_avaliacoes(db, limit, offset, id_produto=id_produto, status="publicada")
    return {**_pagina(_montar(db, storage, linhas), total, limit, offset),
            "media": repo.media_do_produto(db, id_produto)}


# publicada: qualquer pessoa vê; oculta: só quem avaliou (sem as fotos) e a moderação
def detalhar(db: Session, storage, id_avaliacao: int, usuario: Usuario | None) -> dict:
    avaliacao = _avaliacao(db, id_avaliacao)
    moderacao = usuario is not None and usuario_tem_permissao(db, usuario, "moderar_avaliacoes")
    autor = usuario is not None and repo.autor_da_avaliacao(db, id_avaliacao) == usuario.id_usuario
    if avaliacao.status != "publicada" and not (moderacao or autor):
        raise RecursoNaoEncontrado(NAO_ENCONTRADA)
    return _detalhar(db, storage, id_avaliacao, moderacao)


# ---------- plataforma do cliente ----------

# só quem recebeu a peça avalia: uma avaliação por item de pedido entregue da própria conta.
# Compra na loja sem CPF entra na conta pela reivindicação (case, seção 5)
def criar(db: Session, storage, cliente: Usuario, dados: dict) -> dict:
    encontrado = repo.item_com_pedido(db, dados["id_item_pedido"])
    if encontrado is None or encontrado[1].id_cliente != cliente.id_usuario:
        raise RecursoNaoEncontrado("Item não encontrado nos seus pedidos")
    if encontrado[1].status != "entregue":
        raise RegraDeNegocio("Só dá para avaliar depois de receber o pedido")
    if repo.avaliacao_do_item(db, dados["id_item_pedido"]) is not None:
        raise Conflito("Este item já foi avaliado; edite a avaliação existente")

    avaliacao = Avaliacao(**dados, status="publicada")
    db.add(avaliacao)
    db.commit()
    return _detalhar(db, storage, avaliacao.id_avaliacao)


def alterar(db: Session, storage, cliente: Usuario, id_avaliacao: int, campos: dict) -> dict:
    avaliacao = _minha_para_editar(db, cliente, id_avaliacao)
    for campo, valor in campos.items():
        setattr(avaliacao, campo, valor)
    avaliacao.editada_em = _agora()
    db.commit()
    return _detalhar(db, storage, id_avaliacao)


# até 5 fotos, nos 7 dias de edição; vão para a área privada do Storage
def adicionar_foto(db: Session, storage, cliente: Usuario, id_avaliacao: int, arquivo) -> dict:
    _minha_para_editar(db, cliente, id_avaliacao)
    usadas = {f.ordem for f in repo.fotos_das_avaliacoes(db, [id_avaliacao])[id_avaliacao]}
    livres = [ordem for ordem in range(1, MAXIMO_DE_FOTOS + 1) if ordem not in usadas]
    if not livres:
        raise RegraDeNegocio("A avaliação já tem 5 fotos")

    caminho = arquivos.enviar(storage, FOTO_AVALIACAO, f"avaliacao-{id_avaliacao}", arquivo)
    try:
        db.add(FotoAvaliacao(id_avaliacao=id_avaliacao, caminho_arquivo=caminho, ordem=livres[0]))
        db.commit()
    except Exception:
        db.rollback()
        arquivos.desfazer_envio(storage, FOTO_AVALIACAO, caminho)
        raise
    return _detalhar(db, storage, id_avaliacao)


def votar_util(db: Session, storage, cliente: Usuario, id_avaliacao: int) -> dict:
    _de_outra_pessoa(db, cliente, id_avaliacao)
    if repo.buscar_voto(db, id_avaliacao, cliente.id_usuario) is not None:
        raise Conflito("Você já marcou esta avaliação como útil")
    db.add(VotoUtil(id_avaliacao=id_avaliacao, id_cliente=cliente.id_usuario))
    db.commit()
    return _detalhar(db, storage, id_avaliacao)


def denunciar(db: Session, cliente: Usuario, id_avaliacao: int, motivo: str) -> dict:
    _de_outra_pessoa(db, cliente, id_avaliacao)
    if repo.denuncia_do_cliente(db, id_avaliacao, cliente.id_usuario) is not None:
        raise Conflito("Você já denunciou esta avaliação")
    denuncia = DenunciaAvaliacao(id_avaliacao=id_avaliacao, id_cliente=cliente.id_usuario, motivo=motivo,
                                 status="pendente")
    db.add(denuncia)
    db.commit()
    return repo.detalhar_denuncia(db, denuncia.id_denuncia)


# ---------- moderação (moderar_avaliacoes) ----------

def listar_para_moderacao(db: Session, storage, limit: int, offset: int, **filtros) -> dict:
    linhas, total = repo.listar_avaliacoes(db, limit, offset, **filtros)
    return _pagina(_montar(db, storage, linhas, moderacao=True), total, limit, offset)


# a equipe oculta com motivo; as fotos saem do ar (case, seção 5)
def _ocultar(avaliacao: Avaliacao, moderador: Usuario, motivo: str) -> None:
    avaliacao.status = "oculta"
    avaliacao.motivo_ocultacao = motivo
    avaliacao.id_ocultada_por = moderador.id_usuario
    avaliacao.ocultada_em = _agora()


def ocultar(db: Session, storage, moderador: Usuario, id_avaliacao: int, motivo: str) -> dict:
    avaliacao = _avaliacao(db, id_avaliacao, travar=True)
    if avaliacao.status == "oculta":
        raise RegraDeNegocio("A avaliação já está oculta")
    _ocultar(avaliacao, moderador, motivo)
    db.commit()
    return _detalhar(db, storage, id_avaliacao, moderacao=True)


def listar_denuncias(db: Session, limit: int, offset: int, status: str | None) -> dict:
    return _pagina(*repo.listar_denuncias(db, limit, offset, status), limit, offset)


# procedente oculta a avaliação (se ainda estiver publicada); improcedente só encerra a denúncia
def analisar_denuncia(db: Session, moderador: Usuario, id_denuncia: int, procedente: bool,
                      motivo_ocultacao: str | None) -> dict:
    denuncia = repo.buscar_denuncia(db, id_denuncia, travar=True)
    if denuncia is None:
        raise RecursoNaoEncontrado("Denúncia não encontrada")
    if denuncia.status != "pendente":
        raise Conflito("Esta denúncia já foi analisada")

    denuncia.status = "procedente" if procedente else "improcedente"
    denuncia.id_analisada_por = moderador.id_usuario
    denuncia.analisada_em = _agora()
    if procedente:
        avaliacao = _avaliacao(db, denuncia.id_avaliacao, travar=True)
        if avaliacao.status == "publicada":
            _ocultar(avaliacao, moderador, motivo_ocultacao or denuncia.motivo)
    db.commit()
    return repo.detalhar_denuncia(db, id_denuncia)
