from sqlalchemy.orm import Session

from src.models.contas import ModeloAcesso
from src.repositories import modelo_acesso_repository as repo
from src.use_cases.erros import Conflito, RecursoNaoEncontrado, RegraDeNegocio
from src.use_cases.permissoes import PERMISSOES_SO_ADMIN


def listar_permissoes(db: Session) -> list[dict]:
    return [
        {"codigo": p.codigo, "descricao": p.descricao, "so_admin": p.codigo in PERMISSOES_SO_ADMIN}
        for p in repo.listar_permissoes(db)
    ]


def _montar(modelo: ModeloAcesso, codigos: dict[int, list[str]], pessoas: dict[int, int], todas: list[str]) -> dict:
    return {
        "id_modelo": modelo.id_modelo,
        "nome": modelo.nome,
        "eh_admin": modelo.eh_admin,
        "ativo": modelo.ativo,
        "pessoas": pessoas.get(modelo.id_modelo, 0),
        "permissoes": todas if modelo.eh_admin else codigos.get(modelo.id_modelo, []),
    }


def listar_modelos(db: Session) -> list[dict]:
    codigos, pessoas = repo.codigos_por_modelo(db), repo.pessoas_por_modelo(db)
    todas = [p.codigo for p in repo.listar_permissoes(db)]
    return [_montar(m, codigos, pessoas, todas) for m in repo.listar_modelos(db)]


def buscar_modelo(db: Session, id_modelo: int) -> dict:
    modelo = _modelo_ou_404(db, id_modelo)
    todas = [p.codigo for p in repo.listar_permissoes(db)]
    return _montar(modelo, repo.codigos_por_modelo(db), repo.pessoas_por_modelo(db), todas)


def _modelo_ou_404(db: Session, id_modelo: int) -> ModeloAcesso:
    modelo = repo.buscar_modelo(db, id_modelo)
    if modelo is None:
        raise RecursoNaoEncontrado("Modelo de acesso não encontrado")
    return modelo


# códigos válidos e fora da Gestão (permissões da Gestão são só do Admin)
def _ids_das_permissoes(db: Session, codigos: list[str]) -> list[int]:
    codigos = sorted(set(codigos))
    da_gestao = [c for c in codigos if c in PERMISSOES_SO_ADMIN]
    if da_gestao:
        raise RegraDeNegocio(f"Permissões da Gestão são só do Admin: {', '.join(da_gestao)}")
    ids = repo.ids_das_permissoes(db, codigos)
    desconhecidos = [c for c in codigos if c not in ids]
    if desconhecidos:
        raise RegraDeNegocio(f"Permissão desconhecida: {', '.join(desconhecidos)}")
    return [ids[c] for c in codigos]


def _conferir_nome(db: Session, nome: str, id_modelo: int | None = None) -> None:
    outro = repo.modelo_por_nome(db, nome)
    if outro is not None and outro.id_modelo != id_modelo:
        raise Conflito("Já existe um modelo de acesso com esse nome")


def criar_modelo(db: Session, nome: str, permissoes: list[str]) -> dict:
    _conferir_nome(db, nome)
    ids = _ids_das_permissoes(db, permissoes)
    modelo = ModeloAcesso(nome=nome, eh_admin=False)
    db.add(modelo)
    db.flush()
    repo.trocar_permissoes(db, modelo.id_modelo, ids)
    db.commit()
    return buscar_modelo(db, modelo.id_modelo)


# o banco recusa desativar modelo com pessoas ligadas e tirar o Admin de admin
def alterar_modelo(db: Session, id_modelo: int, campos: dict) -> dict:
    modelo = _modelo_ou_404(db, id_modelo)
    if "nome" in campos:
        _conferir_nome(db, campos["nome"], id_modelo)
    for campo, valor in campos.items():
        setattr(modelo, campo, valor)
    db.commit()
    return buscar_modelo(db, id_modelo)


def trocar_permissoes(db: Session, id_modelo: int, permissoes: list[str]) -> dict:
    modelo = _modelo_ou_404(db, id_modelo)
    if modelo.eh_admin:
        raise RegraDeNegocio("O Admin tem todas as permissões, inclusive as futuras; não dá para editar")
    repo.trocar_permissoes(db, id_modelo, _ids_das_permissoes(db, permissoes))
    db.commit()
    return buscar_modelo(db, id_modelo)
