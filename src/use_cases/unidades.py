from sqlalchemy.orm import Session

from src.models.estoque import Unidade
from src.repositories import unidade_repository as repo
from src.use_cases.erros import Conflito, RecursoNaoEncontrado, RegraDeNegocio


def buscar_unidade(db: Session, id_unidade: int) -> Unidade:
    unidade = repo.buscar_unidade(db, id_unidade)
    if unidade is None:
        raise RecursoNaoEncontrado("Unidade não encontrada")
    return unidade


def criar_unidade(db: Session, dados: dict) -> Unidade:
    if repo.unidade_por_nome(db, dados["nome"]) is not None:
        raise Conflito("Unidade já existe")
    unidade = Unidade(**dados)
    db.add(unidade)
    db.commit()
    db.refresh(unidade)
    return unidade


# o tipo não muda; desativar = ativo false (unidade não é apagada)
def alterar_unidade(db: Session, id_unidade: int, campos: dict) -> Unidade:
    unidade = buscar_unidade(db, id_unidade)
    nome = campos.get("nome")
    if nome is not None:
        outra = repo.unidade_por_nome(db, nome)
        if outra is not None and outra.id_unidade != id_unidade:
            raise Conflito("Unidade já existe")
    if unidade.tipo == "cd" and campos.get("despacha_online") is False:
        raise RegraDeNegocio("O CD sempre despacha pedidos online")
    for campo, valor in campos.items():
        setattr(unidade, campo, valor)
    db.commit()
    db.refresh(unidade)
    return unidade
