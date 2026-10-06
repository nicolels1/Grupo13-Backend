import logging
import uuid

from sqlalchemy.orm import Session

from src.models.contas import Usuario, UsuarioPermissaoExcecao
from src.repositories import permissao_repository, unidade_repository, usuario_repository
from src.use_cases.contas import montar_perfil, traduzir_erro_auth
from src.use_cases.erros import (
    Conflito, ErroNegocio, RecursoNaoEncontrado, RegraDeNegocio, ServicoIndisponivel,
)
from src.use_cases.permissoes import PERMISSOES_SO_ADMIN
from src.utils.supabase_admin import ErroSupabase

logger = logging.getLogger(__name__)

CONVITE_FALHOU = (
    "Não foi possível enviar o convite por e-mail. Confira o servidor de e-mail do Supabase "
    "ou crie a conta com senha provisória"
)


def _usuario(db: Session, id_usuario: uuid.UUID) -> Usuario:
    usuario = permissao_repository.buscar_usuario(db, id_usuario)
    if usuario is None:
        raise RecursoNaoEncontrado("Conta não encontrada")
    return usuario


def _conferir_modelo(db: Session, id_modelo: int) -> None:
    modelo = permissao_repository.buscar_modelo(db, id_modelo)
    if modelo is None:
        raise RecursoNaoEncontrado("Modelo de acesso não encontrado")
    if not modelo.ativo:
        raise RegraDeNegocio("Modelo de acesso desativado")


def _conferir_unidade(db: Session, id_unidade: int | None) -> None:
    if id_unidade is None:
        return
    unidade = unidade_repository.buscar_unidade(db, id_unidade)
    if unidade is None:
        raise RecursoNaoEncontrado("Unidade não encontrada")
    if not unidade.ativo:
        raise RegraDeNegocio("Unidade desativada")


def _erro_do_convite(erro: ErroSupabase) -> ErroNegocio:
    if erro.codigo == "email_address_invalid":
        return RegraDeNegocio(CONVITE_FALHOU)
    if erro.status >= 500:
        return ServicoIndisponivel(CONVITE_FALHOU)
    return traduzir_erro_auth(erro)


# ---------- leitura ----------

def listar(db: Session, limit: int, offset: int, **filtros) -> dict:
    itens, total = usuario_repository.listar_usuarios(db, limit, offset, **filtros)
    return {"items": itens, "total": total, "limit": limit, "offset": offset}


# mesmos dados do /me (modelo e permissões efetivas) mais as exceções e se o convite está pendente
def detalhar(db: Session, id_usuario: uuid.UUID) -> dict:
    usuario = _usuario(db, id_usuario)
    excecoes = permissao_repository.excecoes_do_usuario(db, id_usuario)
    return {
        **montar_perfil(db, usuario),
        "excecoes": [{"codigo": c, "efeito": e} for c, e in sorted(excecoes.items())],
        "convite_pendente": not usuario_repository.login_confirmado(db, id_usuario),
    }


# ---------- contas internas ----------

def criar_conta_interna(db: Session, auth, dados: dict) -> dict:
    email = dados["email"]
    if usuario_repository.email_em_uso(db, email) or usuario_repository.buscar_login(db, email) is not None:
        raise Conflito("E-mail já cadastrado")
    _conferir_modelo(db, dados["id_modelo_acesso"])
    _conferir_unidade(db, dados.get("id_unidade"))

    senha = dados.get("senha_provisoria")
    try:
        id_usuario = auth.criar_login(email, senha) if senha else auth.convidar(email)
    except ErroSupabase as erro:
        raise traduzir_erro_auth(erro) if senha else _erro_do_convite(erro)

    usuario = Usuario(
        id_usuario=id_usuario, nome=dados["nome"], email=email, tipo_conta="interna", status_conta="ativa",
        id_modelo_acesso=dados["id_modelo_acesso"], id_unidade=dados.get("id_unidade"),
    )
    try:
        db.add(usuario)
        db.commit()
    except Exception:
        # mesma regra do cadastro de cliente (ADR 0008): sem a linha em USUARIO, o login sai
        db.rollback()
        try:
            auth.apagar_login(id_usuario)
        except ErroSupabase:
            logger.error("login %s ficou sem linha em USUARIO e não pôde ser apagado", id_usuario)
        raise
    return detalhar(db, id_usuario)


def reenviar_convite(db: Session, auth, id_usuario: uuid.UUID) -> dict:
    usuario = _usuario(db, id_usuario)
    if usuario.tipo_conta != "interna":
        raise RegraDeNegocio("Convite é só para conta interna")
    if usuario_repository.login_confirmado(db, id_usuario):
        raise RegraDeNegocio("A pessoa já definiu a senha; para trocar, use a recuperação de senha")
    try:
        auth.convidar(usuario.email)
    except ErroSupabase as erro:
        raise _erro_do_convite(erro)
    return detalhar(db, id_usuario)


# modelo e unidade só em conta interna; ativar e desativar vale para qualquer conta.
# O banco garante as regras do Admin: sempre uma conta ativa no Admin e, ao entrar no
# modelo Admin, a pessoa perde as exceções de retirada (ADRs 0009 e 0010)
def alterar(db: Session, auth, id_usuario: uuid.UUID, campos: dict) -> dict:
    usuario = _usuario(db, id_usuario)
    if usuario.tipo_conta != "interna" and ({"id_modelo_acesso", "id_unidade"} & campos.keys()):
        raise RegraDeNegocio("Modelo de acesso e unidade são só de conta interna")
    if "id_modelo_acesso" in campos and campos["id_modelo_acesso"] != usuario.id_modelo_acesso:
        _conferir_modelo(db, campos["id_modelo_acesso"])
    if "id_unidade" in campos:
        _conferir_unidade(db, campos["id_unidade"])

    status_anterior = usuario.status_conta
    for campo, valor in campos.items():
        setattr(usuario, campo, valor)
    db.commit()

    # bloqueio do login no Auth depois do banco (nenhuma chamada externa dentro de transação);
    # se o Supabase falhar, o status volta, para banco e login não ficarem diferentes
    novo_status = usuario.status_conta
    if novo_status != status_anterior and "inativa" in (novo_status, status_anterior):
        try:
            if novo_status == "inativa":
                auth.bloquear_login(id_usuario)
            else:
                auth.desbloquear_login(id_usuario)
        except ErroSupabase as erro:
            usuario.status_conta = status_anterior
            db.commit()
            raise traduzir_erro_auth(erro)
    return detalhar(db, id_usuario)


# ---------- exceções de permissão ----------

def _conta_e_permissao(db: Session, id_usuario: uuid.UUID, codigo: str):
    usuario = _usuario(db, id_usuario)
    permissao = permissao_repository.buscar_permissao(db, codigo)
    if permissao is None:
        raise RecursoNaoEncontrado("Permissão não encontrada")
    return usuario, permissao


# o banco também recusa (triggers), mas a conferência aqui devolve uma mensagem clara (ADR 0009)
def definir_excecao(db: Session, id_usuario: uuid.UUID, codigo: str, efeito: str) -> dict:
    usuario, permissao = _conta_e_permissao(db, id_usuario, codigo)
    if usuario.tipo_conta != "interna":
        raise RegraDeNegocio("Exceção de permissão só vale para conta interna")
    if codigo in PERMISSOES_SO_ADMIN:
        raise RegraDeNegocio("Permissões da Gestão são só do Admin e não aceitam exceção")
    if efeito == "retirar" and permissao_repository.modelo_eh_admin(db, usuario.id_modelo_acesso):
        raise RegraDeNegocio("O Admin não aceita exceção de retirada")

    excecao = permissao_repository.buscar_excecao(db, id_usuario, permissao.id_permissao)
    if excecao is None:
        db.add(UsuarioPermissaoExcecao(id_usuario=id_usuario, id_permissao=permissao.id_permissao, efeito=efeito))
    else:
        excecao.efeito = efeito
    db.commit()
    return detalhar(db, id_usuario)


def remover_excecao(db: Session, id_usuario: uuid.UUID, codigo: str) -> dict:
    _, permissao = _conta_e_permissao(db, id_usuario, codigo)
    excecao = permissao_repository.buscar_excecao(db, id_usuario, permissao.id_permissao)
    if excecao is None:
        raise RecursoNaoEncontrado("Essa pessoa não tem exceção para essa permissão")
    db.delete(excecao)
    db.commit()
    return detalhar(db, id_usuario)
