from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from sqlalchemy.orm import Session

from src.database.session import get_db
from src.entities.avaliacoes import (
    AnaliseDenuncia, AvaliacaoAlterar, AvaliacaoCriar, AvaliacaoSaida, AvaliacoesDoProduto, DenunciaCriar,
    DenunciaSaida, Ocultacao, StatusAvaliacao, StatusDenuncia,
)
from src.entities.comum import LIMITE_MAXIMO, LIMITE_PADRAO, Pagina, campos_alterados
from src.middlewares.permissoes import exige_cliente, exige_permissao, get_usuario_opcional
from src.models.contas import Usuario
from src.use_cases import avaliacoes
from src.utils.supabase_storage import SupabaseStorage
from src.utils.upload import get_storage, ler_upload

router = APIRouter(tags=["avaliações"])

MODERAR = exige_permissao("moderar_avaliacoes")
ERROS = {
    401: {"description": "Sem login válido"},
    403: {"description": "Sem permissão"},
    404: {"description": "Avaliação, item ou denúncia não encontrada"},
    409: {"description": "Já avaliado, já votado, já denunciado ou já analisada"},
    422: {"description": "Fora do prazo de 7 dias, pedido não entregue, limite de fotos ou arquivo não aceito"},
}


def _paginacao(limit: int = Query(LIMITE_PADRAO, ge=1, le=LIMITE_MAXIMO), offset: int = Query(0, ge=0)):
    return {"limit": limit, "offset": offset}


# ---------- público ----------

@router.get(
    "/produtos/{id_produto}/avaliacoes",
    response_model=AvaliacoesDoProduto,
    description="Avaliações publicadas do produto, mais recentes primeiro, com a média das notas.",
)
def do_produto(
    id_produto: int, paginacao: dict = Depends(_paginacao), db: Session = Depends(get_db),
    storage: SupabaseStorage = Depends(get_storage),
):
    return avaliacoes.do_produto(db, storage, id_produto, **paginacao)


@router.get("/avaliacoes/{id_avaliacao}", response_model=AvaliacaoSaida, responses=ERROS)
def detalhar(
    id_avaliacao: int, usuario: Usuario | None = Depends(get_usuario_opcional), db: Session = Depends(get_db),
    storage: SupabaseStorage = Depends(get_storage),
):
    return avaliacoes.detalhar(db, storage, id_avaliacao, usuario)


# ---------- plataforma do cliente ----------

@router.post(
    "/avaliacoes",
    status_code=status.HTTP_201_CREATED,
    response_model=AvaliacaoSaida,
    responses=ERROS,
    description="Uma avaliação por item de pedido entregue. Publicada na hora.",
)
def avaliar(
    dados: AvaliacaoCriar, cliente: Usuario = Depends(exige_cliente), db: Session = Depends(get_db),
    storage: SupabaseStorage = Depends(get_storage),
):
    return avaliacoes.criar(db, storage, cliente, dados.model_dump())


@router.patch(
    "/avaliacoes/{id_avaliacao}",
    response_model=AvaliacaoSaida,
    responses=ERROS,
    description="Só quem avaliou, nos 7 dias após a publicação. A avaliação não é apagada.",
)
def editar(
    id_avaliacao: int, dados: AvaliacaoAlterar, cliente: Usuario = Depends(exige_cliente),
    db: Session = Depends(get_db), storage: SupabaseStorage = Depends(get_storage),
):
    return avaliacoes.alterar(db, storage, cliente, id_avaliacao, campos_alterados(dados, nullaveis=("texto",)))


@router.post(
    "/avaliacoes/{id_avaliacao}/fotos",
    status_code=status.HTTP_201_CREATED,
    response_model=AvaliacaoSaida,
    responses=ERROS,
    description="Foto (JPG, PNG ou WEBP de até 5 MB) em multipart/form-data. Até 5 por avaliação.",
)
def adicionar_foto(
    id_avaliacao: int, arquivo: UploadFile = File(), cliente: Usuario = Depends(exige_cliente),
    db: Session = Depends(get_db), storage: SupabaseStorage = Depends(get_storage),
):
    return avaliacoes.adicionar_foto(db, storage, cliente, id_avaliacao, ler_upload(arquivo))


@router.post("/avaliacoes/{id_avaliacao}/util", response_model=AvaliacaoSaida, responses=ERROS)
def votar_util(
    id_avaliacao: int, cliente: Usuario = Depends(exige_cliente), db: Session = Depends(get_db),
    storage: SupabaseStorage = Depends(get_storage),
):
    return avaliacoes.votar_util(db, storage, cliente, id_avaliacao)


@router.post(
    "/avaliacoes/{id_avaliacao}/denuncias",
    status_code=status.HTTP_201_CREATED,
    response_model=DenunciaSaida,
    responses=ERROS,
)
def denunciar(
    id_avaliacao: int, dados: DenunciaCriar, cliente: Usuario = Depends(exige_cliente),
    db: Session = Depends(get_db),
):
    return avaliacoes.denunciar(db, cliente, id_avaliacao, dados.motivo)


# ---------- moderação (moderar_avaliacoes) ----------

@router.get("/moderacao/avaliacoes", response_model=Pagina[AvaliacaoSaida], responses=ERROS)
def listar_para_moderacao(
    status_avaliacao: StatusAvaliacao | None = Query(None, alias="status"),
    id_produto: int | None = None,
    com_denuncia_pendente: bool | None = None,
    paginacao: dict = Depends(_paginacao),
    _: Usuario = Depends(MODERAR),
    db: Session = Depends(get_db),
    storage: SupabaseStorage = Depends(get_storage),
):
    return avaliacoes.listar_para_moderacao(
        db, storage, **paginacao, status=status_avaliacao, id_produto=id_produto,
        com_denuncia_pendente=com_denuncia_pendente,
    )


@router.post(
    "/moderacao/avaliacoes/{id_avaliacao}/ocultar",
    response_model=AvaliacaoSaida,
    responses=ERROS,
    description="Oculta com motivo; as fotos saem do ar.",
)
def ocultar(
    id_avaliacao: int, dados: Ocultacao, moderador: Usuario = Depends(MODERAR), db: Session = Depends(get_db),
    storage: SupabaseStorage = Depends(get_storage),
):
    return avaliacoes.ocultar(db, storage, moderador, id_avaliacao, dados.motivo)


@router.get(
    "/moderacao/denuncias",
    response_model=Pagina[DenunciaSaida],
    responses=ERROS,
    description="Padrão: as pendentes, mais antigas primeiro (bloco da Visão Geral).",
)
def listar_denuncias(
    status_denuncia: StatusDenuncia | None = Query("pendente", alias="status"),
    paginacao: dict = Depends(_paginacao),
    _: Usuario = Depends(MODERAR),
    db: Session = Depends(get_db),
):
    return avaliacoes.listar_denuncias(db, **paginacao, status=status_denuncia)


@router.post("/moderacao/denuncias/{id_denuncia}/analisar", response_model=DenunciaSaida, responses=ERROS)
def analisar(
    id_denuncia: int, dados: AnaliseDenuncia, moderador: Usuario = Depends(MODERAR), db: Session = Depends(get_db),
):
    return avaliacoes.analisar_denuncia(db, moderador, id_denuncia, dados.procedente, dados.motivo_ocultacao)
