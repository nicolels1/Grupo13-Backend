import logging

from sqlalchemy.orm import Session

from src.models.contas import Usuario
from src.repositories import permissao_repository, usuario_repository
from src.use_cases.erros import (
    Conflito, ErroNegocio, MuitasTentativas, NaoAutenticado, RegraDeNegocio, SemPermissao,
    ServicoIndisponivel,
)
from src.use_cases.permissoes import permissoes_efetivas
from src.utils.supabase_admin import ErroSupabase

logger = logging.getLogger(__name__)

LOGIN_INVALIDO = "CPF ou senha inválidos"


# respostas do Supabase Auth viram erros com mensagem para o usuário (usado também na Gestão)
def traduzir_erro_auth(erro: ErroSupabase) -> ErroNegocio:
    if erro.codigo == "email_exists":
        return Conflito("E-mail já cadastrado")
    if erro.codigo == "weak_password":
        return RegraDeNegocio("Senha fraca: use pelo menos 6 caracteres")
    if erro.status == 429:
        return MuitasTentativas("Muitas tentativas. Aguarde alguns minutos e tente de novo")
    if erro.status >= 500:
        return ServicoIndisponivel("Serviço de autenticação indisponível")
    logger.error("Supabase Auth recusou com erro não previsto: %s", erro)
    return RegraDeNegocio("Não foi possível concluir a operação no serviço de autenticação")


# cliente se cadastra pelo site (ADR 0008): o backend valida, cria o login já
# confirmado e grava a linha de USUARIO; se a gravação falhar, apaga o login criado
def cadastrar_cliente(db: Session, auth, nome: str, email: str, cpf: str, senha: str) -> Usuario:
    if usuario_repository.email_em_uso(db, email):
        raise Conflito("E-mail já cadastrado")
    if usuario_repository.cpf_em_uso(db, cpf):
        raise Conflito("CPF já cadastrado")

    try:
        id_usuario = auth.criar_login(email, senha)
    except ErroSupabase as erro:
        raise traduzir_erro_auth(erro)

    usuario = Usuario(
        id_usuario=id_usuario,
        nome=nome,
        email=email,
        cpf=cpf,
        tipo_conta="cliente",
        status_conta="ativa",
    )
    try:
        db.add(usuario)
        db.commit()
    except Exception:
        db.rollback()
        try:
            auth.apagar_login(id_usuario)
        except ErroSupabase:
            # o erro original continua sendo o que importa; o login sem USUARIO fica registrado
            logger.error("login %s ficou sem linha em USUARIO e não pôde ser apagado", id_usuario)
        raise
    return usuario


# login por CPF: o backend acha o e-mail do cliente e pede a sessão ao Supabase,
# sem mostrar o e-mail. CPF inexistente e senha errada dão a mesma resposta.
def entrar_com_cpf(db: Session, auth_login, cpf: str, senha: str) -> dict:
    usuario = usuario_repository.buscar_cliente_por_cpf(db, cpf)
    if usuario is None:
        raise NaoAutenticado(LOGIN_INVALIDO)

    try:
        sessao = auth_login.entrar_com_senha(usuario.email, senha)
    except ErroSupabase as erro:
        if erro.status in (400, 401):
            raise NaoAutenticado(LOGIN_INVALIDO)
        raise traduzir_erro_auth(erro)

    if usuario.status_conta != "ativa":
        raise SemPermissao("Conta não está ativa")
    return sessao


# dados da conta logada para o frontend: tipo de conta, modelo de acesso e permissões
def montar_perfil(db: Session, usuario: Usuario) -> dict:
    modelo = None
    permissoes: set[str] = set()

    if usuario.tipo_conta == "interna" and usuario.id_modelo_acesso is not None:
        modelo = permissao_repository.buscar_modelo(db, usuario.id_modelo_acesso)
        excecoes = permissao_repository.excecoes_do_usuario(db, usuario.id_usuario)
        permissoes = permissoes_efetivas(
            eh_admin=modelo.eh_admin,
            todas=permissao_repository.todos_os_codigos(db) if modelo.eh_admin else set(),
            do_modelo=permissao_repository.codigos_do_modelo(db, modelo.id_modelo),
            acrescentadas={c for c, efeito in excecoes.items() if efeito == "acrescentar"},
            retiradas={c for c, efeito in excecoes.items() if efeito == "retirar"},
        )

    return {
        "id_usuario": usuario.id_usuario,
        "nome": usuario.nome,
        "email": usuario.email,
        "tipo_conta": usuario.tipo_conta,
        "status_conta": usuario.status_conta,
        "id_unidade": usuario.id_unidade,
        "modelo_acesso": (
            {"id_modelo": modelo.id_modelo, "nome": modelo.nome, "eh_admin": modelo.eh_admin}
            if modelo else None
        ),
        "permissoes": sorted(permissoes),
    }
