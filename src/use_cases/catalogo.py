from sqlalchemy.orm import Session

from src.models.catalogo import CategoriaProduto
from src.repositories import catalogo_repository as repo
from src.use_cases.erros import Conflito, RecursoNaoEncontrado


def _categoria_ou_404(db: Session, id_categoria: int) -> CategoriaProduto:
    categoria = repo.buscar_categoria(db, id_categoria)
    if categoria is None:
        raise RecursoNaoEncontrado("Categoria não encontrada")
    return categoria


def criar_categoria(db: Session, nome: str) -> CategoriaProduto:
    if repo.categoria_por_nome(db, nome) is not None:
        raise Conflito("Categoria já existe")
    categoria = CategoriaProduto(nome=nome)
    db.add(categoria)
    db.commit()
    db.refresh(categoria)
    return categoria


# muda nome e/ou ativo; desativar esconde a categoria sem apagar (produtos continuam ligados)
def alterar_categoria(db: Session, id_categoria: int, campos: dict) -> CategoriaProduto:
    categoria = _categoria_ou_404(db, id_categoria)
    nome = campos.get("nome")
    if nome is not None:
        outra = repo.categoria_por_nome(db, nome)
        if outra is not None and outra.id_categoria != id_categoria:
            raise Conflito("Categoria já existe")
    for campo, valor in campos.items():
        setattr(categoria, campo, valor)
    db.commit()
    db.refresh(categoria)
    return categoria
