import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from src.database.session import get_db
from src.entities.comum import LIMITE_MAXIMO, LIMITE_PADRAO, Pagina, campos_alterados
from src.entities.usuarios import (
    ContaInternaCriar, ExcecaoDefinir, StatusConta, TipoConta, UsuarioAlterar, UsuarioDetalhe, UsuarioItem,
)
from src.middlewares.permissoes import exige_permissao
from src.models.contas import Usuario
from src.routes.contas import get_supabase_admin
from src.use_cases import usuarios
from src.utils.supabase_admin import SupabaseAdmin

router = APIRouter(tags=["gestão de contas"])

# Gestão: só o Admin tem gerenciar_contas
so_admin = exige_permissao("gerenciar_contas")
ERROS = {
    401: {"description": "Sem login válido"},
    403: {"description": "Sem permissão"},
    404: {"description": "Conta, modelo, unidade ou permissão não encontrada"},
    409: {"description": "E-mail já cadastrado"},
    422: {"description": "Dados inválidos ou regra das contas (ex.: último Admin ativo)"},
    503: {"description": "Supabase Auth indisponível ou convite não enviado"},
}


@router.get("/usuarios", response_model=Pagina[UsuarioItem], responses=ERROS)
def listar(
    tipo_conta: TipoConta | None = None,
    status_conta: StatusConta | None = None,
    id_modelo_acesso: int | None = None,
    id_unidade: int | None = None,
    busca: str | None = Query(None, max_length=100, description="Parte do nome ou do e-mail"),
    limit: int = Query(LIMITE_PADRAO, ge=1, le=LIMITE_MAXIMO),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: Usuario = Depends(so_admin),
):
    return usuarios.listar(
        db, limit, offset, tipo_conta=tipo_conta, status_conta=status_conta, id_modelo_acesso=id_modelo_acesso,
        id_unidade=id_unidade, busca=busca,
    )


@router.get("/usuarios/{id_usuario}", response_model=UsuarioDetalhe, responses=ERROS)
def detalhar(id_usuario: uuid.UUID, db: Session = Depends(get_db), _: Usuario = Depends(so_admin)):
    return usuarios.detalhar(db, id_usuario)


@router.post("/usuarios", status_code=status.HTTP_201_CREATED, response_model=UsuarioDetalhe, responses=ERROS)
def criar_conta_interna(
    dados: ContaInternaCriar,
    db: Session = Depends(get_db),
    auth: SupabaseAdmin = Depends(get_supabase_admin),
    _: Usuario = Depends(so_admin),
):
    return usuarios.criar_conta_interna(db, auth, dados.model_dump())


@router.patch("/usuarios/{id_usuario}", response_model=UsuarioDetalhe, responses=ERROS)
def alterar(
    id_usuario: uuid.UUID,
    dados: UsuarioAlterar,
    db: Session = Depends(get_db),
    auth: SupabaseAdmin = Depends(get_supabase_admin),
    _: Usuario = Depends(so_admin),
):
    return usuarios.alterar(db, auth, id_usuario, campos_alterados(dados, nullaveis=("id_unidade",)))


@router.post(
    "/usuarios/{id_usuario}/reenviar-convite",
    response_model=UsuarioDetalhe,
    responses=ERROS,
    description="Para conta interna que ainda não definiu a senha. O link do convite vale 24h.",
)
def reenviar_convite(
    id_usuario: uuid.UUID,
    db: Session = Depends(get_db),
    auth: SupabaseAdmin = Depends(get_supabase_admin),
    _: Usuario = Depends(so_admin),
):
    return usuarios.reenviar_convite(db, auth, id_usuario)


@router.put(
    "/usuarios/{id_usuario}/excecoes/{codigo}",
    response_model=UsuarioDetalhe,
    responses=ERROS,
    description="Acrescenta ou retira uma permissão só desta pessoa, além do modelo de acesso.",
)
def definir_excecao(
    id_usuario: uuid.UUID, codigo: str, dados: ExcecaoDefinir, db: Session = Depends(get_db),
    _: Usuario = Depends(so_admin),
):
    return usuarios.definir_excecao(db, id_usuario, codigo, dados.efeito)


@router.delete("/usuarios/{id_usuario}/excecoes/{codigo}", response_model=UsuarioDetalhe, responses=ERROS)
def remover_excecao(
    id_usuario: uuid.UUID, codigo: str, db: Session = Depends(get_db), _: Usuario = Depends(so_admin)
):
    return usuarios.remover_excecao(db, id_usuario, codigo)
