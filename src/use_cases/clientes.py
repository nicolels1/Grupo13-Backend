import logging
import uuid

from sqlalchemy.orm import Session

from src.config.settings import URL_ATIVACAO
from src.models.contas import Usuario
from src.repositories import permissao_repository, usuario_repository
from src.repositories import pedido_repository
from src.use_cases.contas import traduzir_erro_auth
from src.use_cases.erros import Conflito, RecursoNaoEncontrado, RegraDeNegocio, SemPermissao
from src.utils.cpf import cpf_valido, normalizar_cpf
from src.utils.supabase_admin import ErroSupabase

logger = logging.getLogger(__name__)

PENDENTE = "pendente_ativacao"


def _resposta(usuario: Usuario, link: str | None = None, id_conta_mantida=None) -> dict:
    return {
        "id_usuario": usuario.id_usuario, "nome": usuario.nome, "email": usuario.email, "cpf": usuario.cpf,
        "status_conta": usuario.status_conta, "link_ativacao": link, "id_conta_mantida": id_conta_mantida,
    }


def _cliente(db: Session, id_usuario: uuid.UUID) -> Usuario:
    usuario = permissao_repository.buscar_usuario(db, id_usuario)
    if usuario is None or usuario.tipo_conta != "cliente":
        raise RecursoNaoEncontrado("Cliente não encontrado")
    return usuario


# no caixa, basta o CPF para achar o cliente nas próximas compras (case, seção 5)
def buscar_por_cpf(db: Session, cpf: str) -> Usuario:
    cpf = normalizar_cpf(cpf)
    if not cpf_valido(cpf):
        raise RegraDeNegocio("CPF inválido")
    cliente = usuario_repository.buscar_cliente_por_cpf(db, cpf)
    if cliente is None:
        raise RecursoNaoEncontrado("Nenhum cliente com esse CPF")
    return cliente


def _link(auth, tipo: str, email: str) -> tuple[uuid.UUID, str]:
    try:
        return auth.gerar_link(tipo, email, URL_ATIVACAO)
    except ErroSupabase as erro:
        raise traduzir_erro_auth(erro)


# primeira compra na loja: nome, CPF e e-mail, conta sem senha. O login nasce no Auth com um link de
# primeiro acesso, e a conta só faz a ativação até confirmar o CPF (case, seção 5; ADR 0008)
def cadastrar_no_caixa(db: Session, auth, dados: dict) -> dict:
    if usuario_repository.cpf_em_uso(db, dados["cpf"]):
        raise Conflito("CPF já cadastrado")
    if usuario_repository.email_em_uso(db, dados["email"]) or usuario_repository.buscar_login(db, dados["email"]):
        raise Conflito("E-mail já cadastrado")

    id_usuario, link = _link(auth, "invite", dados["email"])
    usuario = Usuario(id_usuario=id_usuario, nome=dados["nome"], email=dados["email"], cpf=dados["cpf"],
                      tipo_conta="cliente", status_conta=PENDENTE)
    try:
        db.add(usuario)
        db.commit()
    except Exception:
        db.rollback()
        try:
            auth.apagar_login(id_usuario)
        except ErroSupabase:
            logger.error("login %s ficou sem linha em USUARIO e não pôde ser apagado", id_usuario)
        raise
    return _resposta(usuario, link)


# o link anterior pode ter vencido ou se perdido: gera outro (case: links podem ser reenviados)
def novo_link(db: Session, auth, id_usuario: uuid.UUID) -> dict:
    cliente = _cliente(db, id_usuario)
    if cliente.status_conta != PENDENTE:
        raise RegraDeNegocio("A conta já foi ativada; para trocar a senha, use a recuperação de senha")
    _, link = _link(auth, "magiclink", cliente.email)
    return _resposta(cliente, link)


# e-mail ou CPF errados são corrigidos em qualquer loja, com documento. Se o CPF certo já tem conta,
# os pedidos passam para ela e a conta errada é desativada (case, seção 5)
def corrigir(db: Session, auth, id_usuario: uuid.UUID, campos: dict) -> dict:
    cliente = _cliente(db, id_usuario)
    cpf, email = campos.get("cpf"), campos.get("email")

    if cpf and cpf != cliente.cpf:
        dono = usuario_repository.buscar_cliente_por_cpf(db, cpf)
        if dono is not None:
            return _juntar_na_conta_certa(db, auth, cliente, dono)

    if email and email != cliente.email:
        if usuario_repository.email_em_uso(db, email) or usuario_repository.buscar_login(db, email):
            raise Conflito("E-mail já cadastrado")
        try:
            auth.alterar_email(cliente.id_usuario, email)
        except ErroSupabase as erro:
            raise traduzir_erro_auth(erro)
        cliente.email = email
    if cpf:
        cliente.cpf = cpf
    db.commit()

    # conta ainda não ativada: corrigir o CPF ou o e-mail gera um link novo (case, seção 5)
    link = None
    if cliente.status_conta == PENDENTE and (cpf or email):
        _, link = _link(auth, "magiclink", cliente.email)
    return _resposta(cliente, link)


def _juntar_na_conta_certa(db: Session, auth, errada: Usuario, certa: Usuario) -> dict:
    pedido_repository.transferir_pedidos(db, errada.id_usuario, certa.id_usuario)
    bloquear = errada.status_conta == "ativa"
    errada.status_conta = "inativa"
    db.commit()
    if bloquear:
        try:
            auth.bloquear_login(errada.id_usuario)
        except ErroSupabase as erro:
            # os pedidos já estão na conta certa; o bloqueio pode ser refeito pela Gestão
            logger.error("conta %s desativada, mas o login não foi bloqueado: %s", errada.id_usuario, erro)
    return _resposta(errada, id_conta_mantida=certa.id_usuario)


# ativação da conta do caixa: o link do Supabase já identificou a pessoa (token); ela confirma o CPF
# e define a senha. Sem limite de tentativas: o link vale pelo prazo do Supabase (case, seção 4)
def ativar(db: Session, auth, id_usuario: str, cpf: str, senha: str) -> Usuario:
    try:
        usuario = permissao_repository.buscar_usuario(db, uuid.UUID(id_usuario))
    except ValueError:
        usuario = None
    if usuario is None or usuario.tipo_conta != "cliente":
        raise SemPermissao("Usuário não cadastrado")
    if usuario.status_conta != PENDENTE:
        raise RegraDeNegocio("Esta conta já foi ativada")
    if usuario.cpf != cpf:
        raise RegraDeNegocio("O CPF não confere com o cadastro feito na loja")

    try:
        auth.definir_senha(usuario.id_usuario, senha)
    except ErroSupabase as erro:
        raise traduzir_erro_auth(erro)
    usuario.status_conta = "ativa"
    db.commit()
    return usuario
