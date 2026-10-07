import logging
import uuid

from sqlalchemy.orm import Session

from src.models.contas import Usuario
from src.repositories import permissao_repository, usuario_repository
from src.repositories import pedido_repository
from src.use_cases.contas import traduzir_erro_auth
from src.use_cases.erros import Conflito, RecursoNaoEncontrado, RegraDeNegocio
from src.utils.cpf import cpf_valido, normalizar_cpf
from src.utils.supabase_admin import ErroSupabase

logger = logging.getLogger(__name__)


def _resposta(usuario: Usuario, compras_ligadas: int = 0, id_conta_mantida=None) -> dict:
    return {
        "id_usuario": usuario.id_usuario, "nome": usuario.nome, "email": usuario.email, "cpf": usuario.cpf,
        "status_conta": usuario.status_conta, "id_conta_mantida": id_conta_mantida,
        "compras_ligadas": compras_ligadas,
    }


def _cliente(db: Session, id_usuario: uuid.UUID) -> Usuario:
    usuario = permissao_repository.buscar_usuario(db, id_usuario)
    if usuario is None or usuario.tipo_conta != "cliente":
        raise RecursoNaoEncontrado("Cliente não encontrado")
    return usuario


# no caixa, o CPF mostra se o cliente já tem conta; sem conta, a venda guarda o CPF na nota (ADR 0014)
def buscar_por_cpf(db: Session, cpf: str) -> Usuario:
    cpf = normalizar_cpf(cpf)
    if not cpf_valido(cpf):
        raise RegraDeNegocio("CPF inválido")
    cliente = usuario_repository.buscar_cliente_por_cpf(db, cpf)
    if cliente is None:
        raise RecursoNaoEncontrado("Nenhum cliente com esse CPF")
    return cliente


# e-mail ou CPF errados são corrigidos em qualquer loja, com documento. Se o CPF certo já tem conta,
# os pedidos passam para ela e a conta errada é desativada (case, seção 5). As compras da loja com o
# CPF certo na nota passam para a conta que fica com ele (ADR 0014)
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

    ligadas = 0
    if cpf and cpf != cliente.cpf:
        cliente.cpf = cpf
        ligadas = pedido_repository.ligar_pedidos_pelo_cpf(db, cpf, cliente.id_usuario)
    db.commit()
    return _resposta(cliente, ligadas)


def _juntar_na_conta_certa(db: Session, auth, errada: Usuario, certa: Usuario) -> dict:
    pedido_repository.transferir_pedidos(db, errada.id_usuario, certa.id_usuario)
    ligadas = pedido_repository.ligar_pedidos_pelo_cpf(db, certa.cpf, certa.id_usuario)
    bloquear = errada.status_conta == "ativa"
    errada.status_conta = "inativa"
    db.commit()
    if bloquear:
        try:
            auth.bloquear_login(errada.id_usuario)
        except ErroSupabase as erro:
            # os pedidos já estão na conta certa; o bloqueio pode ser refeito pela Gestão
            logger.error("conta %s desativada, mas o login não foi bloqueado: %s", errada.id_usuario, erro)
    return _resposta(errada, ligadas, id_conta_mantida=certa.id_usuario)
