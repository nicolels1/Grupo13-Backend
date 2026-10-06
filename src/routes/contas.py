from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from src.database.session import get_db
from src.entities.contas import CadastroCliente, LoginCpf, Perfil, Sessao, UsuarioSaida
from src.middlewares.permissoes import get_usuario_ativo
from src.models.contas import Usuario
from src.use_cases import contas
from src.utils.supabase_admin import SupabaseAdmin, SupabaseLogin

router = APIRouter(tags=["contas"])


# clientes do Supabase como dependências, para os testes trocarem por versões falsas
def get_supabase_admin() -> SupabaseAdmin:
    return SupabaseAdmin()


def get_supabase_login() -> SupabaseLogin:
    return SupabaseLogin()


@router.post(
    "/clientes",
    status_code=status.HTTP_201_CREATED,
    response_model=UsuarioSaida,
    responses={409: {"description": "E-mail ou CPF já cadastrado"}},
)
def cadastrar_cliente(
    dados: CadastroCliente,
    db: Session = Depends(get_db),
    auth: SupabaseAdmin = Depends(get_supabase_admin),
):
    return contas.cadastrar_cliente(db, auth, dados.nome, dados.email, dados.cpf, dados.senha)


@router.post(
    "/login/cpf",
    response_model=Sessao,
    responses={401: {"description": "CPF ou senha inválidos"}, 403: {"description": "Conta não está ativa"}},
)
def entrar_com_cpf(
    dados: LoginCpf,
    db: Session = Depends(get_db),
    auth_login: SupabaseLogin = Depends(get_supabase_login),
):
    return contas.entrar_com_cpf(db, auth_login, dados.cpf, dados.senha)


@router.get(
    "/me",
    response_model=Perfil,
    responses={401: {"description": "Sem login válido"}, 403: {"description": "Conta não cadastrada ou não ativa"}},
)
def meu_perfil(usuario: Usuario = Depends(get_usuario_ativo), db: Session = Depends(get_db)):
    return contas.montar_perfil(db, usuario)
