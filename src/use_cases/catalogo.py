import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from src.models.catalogo import CategoriaProduto, HistoricoPreco, Produto, Variante
from src.repositories import catalogo_repository as repo
from src.repositories import estoque_repository
from src.use_cases.erros import Conflito, RecursoNaoEncontrado, RegraDeNegocio
from src.use_cases.estoque import _validar_local


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


# ---------- produto e variante ----------

def _produto_ou_404(db: Session, id_produto: int) -> Produto:
    produto = repo.buscar_produto(db, id_produto)
    if produto is None:
        raise RecursoNaoEncontrado("Produto não encontrado")
    return produto


def _variante_ou_404(db: Session, id_variante: int) -> Variante:
    variante = repo.buscar_variante(db, id_variante)
    if variante is None:
        raise RecursoNaoEncontrado("Variante não encontrada")
    return variante


# produto novo só entra numa categoria ativa
def _conferir_categoria(db: Session, id_categoria: int) -> None:
    categoria = repo.buscar_categoria(db, id_categoria)
    if categoria is None:
        raise RecursoNaoEncontrado("Categoria não encontrada")
    if not categoria.ativo:
        raise RegraDeNegocio("Categoria desativada")


def montar_produto(produto: Produto, variantes: list[Variante]) -> dict:
    return {
        "id_produto": produto.id_produto,
        "id_categoria": produto.id_categoria,
        "nome": produto.nome,
        "descricao_tecnica": produto.descricao_tecnica,
        "descricao_cliente": produto.descricao_cliente,
        "ativo": produto.ativo,
        "variantes": variantes,
    }


def listar_produtos(db: Session, limit: int, offset: int, **filtros) -> dict:
    produtos, total = repo.listar_produtos(db, limit, offset, **filtros)
    variantes = repo.variantes_dos_produtos(db, [p.id_produto for p in produtos])
    itens = [montar_produto(p, variantes[p.id_produto]) for p in produtos]
    return {"items": itens, "total": total, "limit": limit, "offset": offset}


def buscar_produto(db: Session, id_produto: int) -> dict:
    produto = _produto_ou_404(db, id_produto)
    return montar_produto(produto, repo.variantes_dos_produtos(db, [id_produto])[id_produto])


def _conferir_variantes_novas(db: Session, id_produto: int | None, variantes: list[dict]) -> None:
    skus = [v["sku"] for v in variantes]
    combinacoes = [(v["cor"].lower(), v["tamanho"].lower()) for v in variantes]
    if len(set(skus)) != len(skus):
        raise Conflito("SKU repetido na lista de variantes")
    if len(set(combinacoes)) != len(combinacoes):
        raise Conflito("Cor e tamanho repetidos na lista de variantes")
    for variante in variantes:
        if repo.variante_por_sku(db, variante["sku"]) is not None:
            raise Conflito(f"SKU {variante['sku']} já existe")
        if id_produto is not None and repo.variante_por_cor_e_tamanho(
            db, id_produto, variante["cor"], variante["tamanho"]
        ) is not None:
            raise Conflito(f"Variante {variante['cor']} / {variante['tamanho']} já existe neste produto")


# toda variante nasce com o primeiro registro do histórico de preço (preco_anterior vazio);
# o estoque inicial entra como movimentação saldo_inicial, e o trigger cria a linha de ESTOQUE
def _criar_variante(db: Session, id_produto: int, id_usuario: uuid.UUID, dados: dict) -> Variante:
    estoque_inicial = dados.pop("estoque_inicial", [])
    variante = Variante(id_produto=id_produto, **dados)
    db.add(variante)
    db.flush()
    db.add(HistoricoPreco(id_variante=variante.id_variante, preco_novo=variante.preco, id_alterado_por=id_usuario))
    for carga in estoque_inicial:
        _validar_local(db, variante.id_variante, carga["id_unidade"], carga["canal"])
        estoque_repository.inserir_movimentacao(
            db, id_variante=variante.id_variante, id_unidade=carga["id_unidade"], canal=carga["canal"],
            tipo="saldo_inicial", quantidade=carga["quantidade"], id_usuario=id_usuario,
        )
    return variante


def criar_produto(db: Session, id_usuario: uuid.UUID, dados: dict) -> dict:
    _conferir_categoria(db, dados["id_categoria"])
    variantes = dados.pop("variantes", [])
    _conferir_variantes_novas(db, None, variantes)

    produto = Produto(**dados)
    db.add(produto)
    db.flush()
    criadas = [_criar_variante(db, produto.id_produto, id_usuario, v) for v in variantes]
    db.commit()
    db.refresh(produto)
    for variante in criadas:
        db.refresh(variante)
    return montar_produto(produto, criadas)


def alterar_produto(db: Session, id_produto: int, campos: dict) -> dict:
    produto = _produto_ou_404(db, id_produto)
    if "id_categoria" in campos and campos["id_categoria"] != produto.id_categoria:
        _conferir_categoria(db, campos["id_categoria"])
    for campo, valor in campos.items():
        setattr(produto, campo, valor)
    db.commit()
    return buscar_produto(db, id_produto)


def adicionar_variante(db: Session, id_produto: int, id_usuario: uuid.UUID, dados: dict) -> Variante:
    _produto_ou_404(db, id_produto)
    _conferir_variantes_novas(db, id_produto, [dados])
    variante = _criar_variante(db, id_produto, id_usuario, dados)
    db.commit()
    db.refresh(variante)
    return variante


# mudar o preço grava o histórico (anterior → novo, quem mudou)
def alterar_variante(db: Session, id_variante: int, id_usuario: uuid.UUID, campos: dict) -> Variante:
    variante = _variante_ou_404(db, id_variante)
    if "sku" in campos and campos["sku"] != variante.sku:
        outra = repo.variante_por_sku(db, campos["sku"])
        if outra is not None and outra.id_variante != id_variante:
            raise Conflito(f"SKU {campos['sku']} já existe")
    if "preco" in campos and campos["preco"] != variante.preco:
        db.add(HistoricoPreco(
            id_variante=id_variante, preco_anterior=variante.preco,
            preco_novo=campos["preco"], id_alterado_por=id_usuario,
        ))
    for campo, valor in campos.items():
        setattr(variante, campo, valor)
    db.commit()
    db.refresh(variante)
    return variante


def historico_preco(db: Session, id_variante: int, em: datetime | None) -> list[HistoricoPreco]:
    _variante_ou_404(db, id_variante)
    return repo.historico_preco(db, id_variante, em)
