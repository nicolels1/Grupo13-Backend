from sqlalchemy.orm import Session

from src.models.catalogo import Destaque
from src.repositories import destaque_repository as repo
from src.use_cases import arquivos
from src.use_cases.arquivos import FOTO_PRODUTO
from src.use_cases.erros import RecursoNaoEncontrado
from src.utils.supabase_storage import url_publica

# slides do carrossel do topo da página inicial. As fotos ficam no bucket público das fotos de
# produto (pasta destaques/), como as fotos de categoria


def _destaque_ou_404(db: Session, id_destaque: int) -> Destaque:
    destaque = repo.buscar_destaque(db, id_destaque)
    if destaque is None:
        raise RecursoNaoEncontrado("Destaque não encontrado")
    return destaque


# a saída leva a URL pública da foto; o banco guarda só o caminho no Storage
def montar_destaque(destaque: Destaque) -> dict:
    return {
        "id_destaque": destaque.id_destaque, "titulo": destaque.titulo, "texto": destaque.texto,
        "rotulo_botao": destaque.rotulo_botao, "link": destaque.link,
        "imagem_url": url_publica(FOTO_PRODUTO.bucket, destaque.caminho_imagem) if destaque.caminho_imagem else None,
        "ordem": destaque.ordem, "ativo": destaque.ativo,
    }


def listar_destaques(db: Session, so_ativos: bool) -> list[dict]:
    return [montar_destaque(d) for d in repo.listar_destaques(db, so_ativos)]


# sem ordem, o slide novo entra no fim do carrossel
def criar_destaque(db: Session, dados: dict) -> Destaque:
    destaque = Destaque(**{**dados, "ordem": dados.get("ordem") or repo.proxima_ordem(db)})
    db.add(destaque)
    db.commit()
    db.refresh(destaque)
    return destaque


def alterar_destaque(db: Session, id_destaque: int, campos: dict) -> Destaque:
    destaque = _destaque_ou_404(db, id_destaque)
    for campo, valor in campos.items():
        setattr(destaque, campo, valor)
    db.commit()
    db.refresh(destaque)
    return destaque


# slide é conteúdo, não histórico: apaga a linha e depois a foto (se o Storage falhar, sobra só
# um arquivo solto, que fica no log)
def remover_destaque(db: Session, storage, id_destaque: int) -> None:
    destaque = _destaque_ou_404(db, id_destaque)
    caminho = destaque.caminho_imagem
    db.delete(destaque)
    db.commit()
    if caminho:
        arquivos.apagar(storage, FOTO_PRODUTO, caminho)


# uma foto por slide. Enviar de novo troca a foto: a antiga sai do Storage depois que o banco já
# aponta para a nova; se o banco falhar, a nova é desfeita e a antiga continua
def trocar_imagem_destaque(db: Session, storage, id_destaque: int, arquivo) -> Destaque:
    destaque = _destaque_ou_404(db, id_destaque)
    antiga = destaque.caminho_imagem
    caminho = arquivos.enviar(storage, FOTO_PRODUTO, f"destaques/destaque-{id_destaque}", arquivo)
    destaque.caminho_imagem = caminho
    try:
        db.commit()
    except Exception:
        db.rollback()
        arquivos.desfazer_envio(storage, FOTO_PRODUTO, caminho)
        raise
    if antiga:
        arquivos.apagar(storage, FOTO_PRODUTO, antiga)
    db.refresh(destaque)
    return destaque
