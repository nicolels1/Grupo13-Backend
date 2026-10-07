from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from sqlalchemy.orm import Session

from src.database.session import get_db
from src.entities.catalogo import (
    CategoriaAlterar, CategoriaCriar, CategoriaSaida, HistoricoPrecoSaida, ImagemAlterar, ImagemSaida,
    OrdemProdutos, ProdutoAlterar, ProdutoCriar, ProdutoSaida, VarianteAlterar, VarianteCriar, VarianteSaida,
)
from src.entities.comum import LIMITE_MAXIMO, LIMITE_PADRAO, Lista, Pagina, campos_alterados
from src.middlewares.permissoes import exige_permissao, get_usuario_opcional
from src.models.contas import Usuario
from src.repositories import catalogo_repository
from src.use_cases import catalogo
from src.utils.supabase_storage import SupabaseStorage
from src.utils.upload import get_storage, ler_upload

router = APIRouter(tags=["catalogo"])

ERROS_CATEGORIA = {404: {"description": "Categoria não encontrada"}, 409: {"description": "Categoria já existe"}}


# público: a vitrine do cliente também lista as categorias
@router.get("/categorias", response_model=Lista[CategoriaSaida])
def listar_categorias(ativo: bool | None = None, db: Session = Depends(get_db)):
    return {"items": catalogo_repository.listar_categorias(db, ativo)}


@router.post(
    "/categorias", status_code=status.HTTP_201_CREATED, response_model=CategoriaSaida, responses=ERROS_CATEGORIA
)
def criar_categoria(
    dados: CategoriaCriar,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(exige_permissao("gerenciar_catalogo")),
):
    return catalogo.criar_categoria(db, dados.nome)


@router.patch("/categorias/{id_categoria}", response_model=CategoriaSaida, responses=ERROS_CATEGORIA)
def alterar_categoria(
    id_categoria: int,
    dados: CategoriaAlterar,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(exige_permissao("gerenciar_catalogo")),
):
    return catalogo.alterar_categoria(db, id_categoria, campos_alterados(dados))


# ---------- produto e variante ----------

ERROS_PRODUTO = {
    404: {"description": "Produto, variante ou categoria não encontrada"},
    409: {"description": "SKU ou cor e tamanho repetidos"},
    422: {"description": "Dados inválidos ou categoria desativada"},
}


# público. Sem login (ou sem gerenciar_catalogo): só produtos e variantes ativos, sem a descrição
# técnica, e o filtro "ativo" é ignorado. Com gerenciar_catalogo: tudo, e "ativo" filtra. Lista paginada
@router.get("/produtos", response_model=Pagina[ProdutoSaida])
def listar_produtos(
    id_categoria: int | None = None,
    ativo: bool | None = Query(default=None, description="Só para quem gerencia o catálogo"),
    busca: str | None = Query(default=None, max_length=100, description="Parte do nome do produto"),
    tamanho: str | None = Query(default=None, max_length=20, description="Só produtos com esse tamanho à venda"),
    disponivel: bool | None = Query(
        default=None, description="true: só produtos com peça para vender online (no tamanho, se informado)"
    ),
    ordem: OrdemProdutos = Query(
        default="nome", description="nome, novidades, menor_preco ou maior_preco (pela variante mais barata)"
    ),
    limit: int = Query(LIMITE_PADRAO, ge=1, le=LIMITE_MAXIMO),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    usuario: Usuario | None = Depends(get_usuario_opcional),
):
    return catalogo.listar_produtos(
        db, limit, offset, publico=catalogo.visao_publica(db, usuario),
        id_categoria=id_categoria, ativo=ativo, busca=busca, tamanho=tamanho, disponivel=disponivel, ordem=ordem,
    )


# público: os tamanhos à venda na vitrine, para o filtro da lista. Fica antes de /produtos/{id_produto}
# para "tamanhos" não ser lido como id
@router.get("/produtos/tamanhos", response_model=Lista[str])
def tamanhos_a_venda(
    id_categoria: int | None = None,
    busca: str | None = Query(default=None, max_length=100, description="Parte do nome do produto"),
    db: Session = Depends(get_db),
):
    return {"items": catalogo.tamanhos_a_venda(db, id_categoria, busca)}


@router.get("/produtos/{id_produto}", response_model=ProdutoSaida, responses={404: ERROS_PRODUTO[404]})
def buscar_produto(
    id_produto: int, db: Session = Depends(get_db), usuario: Usuario | None = Depends(get_usuario_opcional)
):
    return catalogo.buscar_produto(db, id_produto, publico=catalogo.visao_publica(db, usuario))


@router.post("/produtos", status_code=status.HTTP_201_CREATED, response_model=ProdutoSaida, responses=ERROS_PRODUTO)
def criar_produto(
    dados: ProdutoCriar,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(exige_permissao("gerenciar_catalogo")),
):
    return catalogo.criar_produto(db, usuario.id_usuario, dados.model_dump())


@router.patch("/produtos/{id_produto}", response_model=ProdutoSaida, responses=ERROS_PRODUTO)
def alterar_produto(
    id_produto: int,
    dados: ProdutoAlterar,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(exige_permissao("gerenciar_catalogo")),
):
    return catalogo.alterar_produto(db, id_produto, campos_alterados(dados))


@router.post(
    "/produtos/{id_produto}/variantes",
    status_code=status.HTTP_201_CREATED,
    response_model=VarianteSaida,
    responses=ERROS_PRODUTO,
)
def adicionar_variante(
    id_produto: int,
    dados: VarianteCriar,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(exige_permissao("gerenciar_catalogo")),
):
    return catalogo.adicionar_variante(db, id_produto, usuario.id_usuario, dados.model_dump())


@router.patch("/variantes/{id_variante}", response_model=VarianteSaida, responses=ERROS_PRODUTO)
def alterar_variante(
    id_variante: int,
    dados: VarianteAlterar,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(exige_permissao("gerenciar_catalogo")),
):
    return catalogo.alterar_variante(db, id_variante, usuario.id_usuario, campos_alterados(dados))


# histórico completo, mais recente primeiro; com `em`, só o preço que valia naquela data
@router.get(
    "/variantes/{id_variante}/historico-preco",
    response_model=Lista[HistoricoPrecoSaida],
    responses={404: {"description": "Variante não encontrada"}},
)
def historico_preco(
    id_variante: int,
    em: str | None = Query(
        default=None, description="AAAA-MM-DD (fim do dia) ou AAAA-MM-DDTHH:MM, no horário de Brasília"
    ),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(exige_permissao("gerenciar_catalogo")),
):
    return {"items": catalogo.historico_preco(db, id_variante, em)}


# ---------- fotos (gerenciar_catalogo) ----------

ERROS_FOTO = {
    404: {"description": "Produto ou foto não encontrada"},
    422: {"description": "Arquivo não aceito ou cor que o produto não tem"},
    503: {"description": "Storage indisponível"},
}


@router.post(
    "/produtos/{id_produto}/imagens",
    status_code=status.HTTP_201_CREATED,
    response_model=ImagemSaida,
    responses=ERROS_FOTO,
    description="Envia uma foto (JPG, PNG ou WEBP de até 5 MB) como multipart/form-data. "
                "Sem cor, vale para todas; sem ordem, entra no fim.",
)
def adicionar_imagem(
    id_produto: int,
    arquivo: UploadFile = File(),
    cor: str | None = Form(None),
    ordem: int | None = Form(None, ge=1),
    db: Session = Depends(get_db),
    storage: SupabaseStorage = Depends(get_storage),
    usuario: Usuario = Depends(exige_permissao("gerenciar_catalogo")),
):
    return catalogo.adicionar_imagem(db, storage, id_produto, ler_upload(arquivo), cor or None, ordem)


@router.patch("/imagens/{id_imagem}", response_model=ImagemSaida, responses=ERROS_FOTO)
def alterar_imagem(
    id_imagem: int,
    dados: ImagemAlterar,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(exige_permissao("gerenciar_catalogo")),
):
    return catalogo.alterar_imagem(db, id_imagem, campos_alterados(dados, nullaveis=("cor",)))
