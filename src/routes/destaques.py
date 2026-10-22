from fastapi import APIRouter, Depends, File, Response, UploadFile, status
from sqlalchemy.orm import Session

from src.database.session import get_db
from src.dependencies.permissoes import exige_permissao, get_usuario_opcional
from src.dependencies.supabase import get_storage
from src.entities.comum import Lista, campos_alterados
from src.entities.destaques import DestaqueAlterar, DestaqueCriar, DestaqueSaida
from src.models.contas import Usuario
from src.use_cases import destaques
from src.use_cases.catalogo import visao_publica
from src.utils.supabase_storage import SupabaseStorage
from src.utils.upload import ler_upload

router = APIRouter(tags=["destaques"])

ERRO_404 = {404: {"description": "Destaque não encontrado"}}


# público: a página inicial mostra os slides ativos, na ordem. Quem gerencia o catálogo vê também
# os desativados, para editar no interno
@router.get("/destaques", response_model=Lista[DestaqueSaida])
def listar_destaques(db: Session = Depends(get_db), usuario: Usuario | None = Depends(get_usuario_opcional)):
    so_ativos = visao_publica(db, usuario)
    return {"items": destaques.listar_destaques(db, so_ativos)}


@router.post("/destaques", status_code=status.HTTP_201_CREATED, response_model=DestaqueSaida)
def criar_destaque(
    dados: DestaqueCriar,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(exige_permissao("gerenciar_catalogo")),
):
    return destaques.montar_destaque(destaques.criar_destaque(db, dados.model_dump()))


@router.patch("/destaques/{id_destaque}", response_model=DestaqueSaida, responses=ERRO_404)
def alterar_destaque(
    id_destaque: int,
    dados: DestaqueAlterar,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(exige_permissao("gerenciar_catalogo")),
):
    return destaques.montar_destaque(destaques.alterar_destaque(db, id_destaque, campos_alterados(dados)))


@router.delete(
    "/destaques/{id_destaque}", status_code=status.HTTP_204_NO_CONTENT, responses=ERRO_404,
    description="Apaga o slide e a foto dele. Para tirar da loja sem apagar, use ativo false.",
)
def remover_destaque(
    id_destaque: int,
    db: Session = Depends(get_db),
    storage: SupabaseStorage = Depends(get_storage),
    usuario: Usuario = Depends(exige_permissao("gerenciar_catalogo")),
):
    destaques.remover_destaque(db, storage, id_destaque)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/destaques/{id_destaque}/imagem",
    response_model=DestaqueSaida,
    responses={**ERRO_404, 422: {"description": "Arquivo não aceito"}, 503: {"description": "Storage indisponível"}},
    description="Foto do slide (JPG, PNG ou WEBP de até 5 MB), como multipart/form-data. Enviar de novo troca a foto.",
)
def trocar_imagem_destaque(
    id_destaque: int,
    arquivo: UploadFile = File(),
    db: Session = Depends(get_db),
    storage: SupabaseStorage = Depends(get_storage),
    usuario: Usuario = Depends(exige_permissao("gerenciar_catalogo")),
):
    return destaques.montar_destaque(destaques.trocar_imagem_destaque(db, storage, id_destaque, ler_upload(arquivo)))
