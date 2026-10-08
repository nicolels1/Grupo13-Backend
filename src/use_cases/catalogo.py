import re
import unicodedata
import uuid

from sqlalchemy.orm import Session

from src.models.catalogo import CategoriaProduto, HistoricoPreco, ImagemProduto, Produto, Variante
from src.repositories import catalogo_repository as repo
from src.repositories import estoque_repository
from src.use_cases import arquivos
from src.use_cases.arquivos import FOTO_PRODUTO
from src.use_cases.erros import Conflito, RecursoNaoEncontrado, RegraDeNegocio
from src.use_cases.permissoes import usuario_tem_permissao
from src.use_cases.estoque import interpretar_momento, validar_local
from src.utils.supabase_storage import url_publica


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


# ---------- foto da categoria (carrossel da página inicial) ----------

# a saída da categoria leva a URL pública da foto; o banco guarda só o caminho no Storage
def montar_categoria(categoria: CategoriaProduto) -> dict:
    return {
        "id_categoria": categoria.id_categoria, "nome": categoria.nome, "ativo": categoria.ativo,
        "imagem_url": url_publica(FOTO_PRODUTO.bucket, categoria.caminho_imagem) if categoria.caminho_imagem else None,
    }


# uma foto por categoria, no bucket público das fotos de produto (pasta categorias/). Enviar de novo
# troca a foto: a antiga sai do Storage depois que o banco já aponta para a nova
def trocar_imagem_categoria(db: Session, storage, id_categoria: int, arquivo) -> CategoriaProduto:
    categoria = _categoria_ou_404(db, id_categoria)
    antiga = categoria.caminho_imagem
    caminho = arquivos.enviar(storage, FOTO_PRODUTO, f"categorias/categoria-{id_categoria}", arquivo)
    categoria.caminho_imagem = caminho
    try:
        db.commit()
    except Exception:
        db.rollback()
        arquivos.desfazer_envio(storage, FOTO_PRODUTO, caminho)
        raise
    if antiga:
        arquivos.apagar(storage, FOTO_PRODUTO, antiga)
    db.refresh(categoria)
    return categoria


# sem foto, a categoria volta ao bloco de cor no carrossel
def remover_imagem_categoria(db: Session, storage, id_categoria: int) -> CategoriaProduto:
    categoria = _categoria_ou_404(db, id_categoria)
    antiga = categoria.caminho_imagem
    if antiga is None:
        return categoria
    categoria.caminho_imagem = None
    db.commit()
    arquivos.apagar(storage, FOTO_PRODUTO, antiga)
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


# visão pública (vitrine): só produtos ativos de categorias ativas, só variantes ativas e sem a
# descrição técnica. Quem gerencia o catálogo vê tudo, inclusive o que está desativado
def visao_publica(db: Session, usuario) -> bool:
    return usuario is None or not usuario_tem_permissao(db, usuario, "gerenciar_catalogo")


def montar_imagem(imagem: ImagemProduto) -> dict:
    return {
        "id_imagem": imagem.id_imagem, "id_produto": imagem.id_produto, "cor": imagem.cor, "ordem": imagem.ordem,
        "url": url_publica(FOTO_PRODUTO.bucket, imagem.caminho_arquivo),
    }


# disponivel só diz se dá para comprar online, nunca a quantidade; fica vazio quando não foi consultado
def montar_variante(variante: Variante, disponiveis: set[int] | None = None) -> dict:
    return {
        "id_variante": variante.id_variante, "id_produto": variante.id_produto, "sku": variante.sku,
        "cor": variante.cor, "tamanho": variante.tamanho, "preco": variante.preco, "ativo": variante.ativo,
        "disponivel": None if disponiveis is None else variante.id_variante in disponiveis,
    }


def montar_produto(
    produto: Produto, variantes: list[Variante], publico: bool = False, imagens: list[ImagemProduto] = (),
    disponiveis: set[int] | None = None,
) -> dict:
    visiveis = [v for v in variantes if v.ativo] if publico else variantes
    return {
        "id_produto": produto.id_produto,
        "id_categoria": produto.id_categoria,
        "nome": produto.nome,
        "descricao_tecnica": None if publico else produto.descricao_tecnica,
        "descricao_cliente": produto.descricao_cliente,
        "ativo": produto.ativo,
        "variantes": [montar_variante(v, disponiveis) for v in visiveis],
        "imagens": [montar_imagem(i) for i in imagens],
    }


def _disponiveis(db: Session, variantes: dict[int, list[Variante]]) -> set[int]:
    return repo.ids_disponiveis_online(db, [v.id_variante for lista in variantes.values() for v in lista])


# o que a pessoa digitou vira palavras sem acento, minúsculas, só letras e números
# ("Calça  jeans!" → ["calca", "jeans"]); o banco compara com o texto do produto do mesmo jeito
MAXIMO_DE_PALAVRAS = 6


def palavras_da_busca(busca: str | None) -> list[str]:
    if not busca:
        return []
    sem_acento = unicodedata.normalize("NFD", busca.lower())
    sem_acento = "".join(letra for letra in sem_acento if not unicodedata.combining(letra))
    palavras = re.findall(r"[a-z0-9]+", sem_acento)
    return list(dict.fromkeys(palavras))[:MAXIMO_DE_PALAVRAS]


# com busca e sem ordem escolhida, os mais parecidos vêm primeiro; sem busca, por nome
def _ordem_padrao(ordem: str | None, palavras: list[str]) -> str:
    if ordem:
        return ordem
    return "relevancia" if palavras else "nome"


# na vitrine, uma busca sem resultado não termina em lista vazia: tenta as peças parecidas de longe
# e, se nem isso, mostra as novidades. busca_alternativa diz qual das duas, para a loja avisar que
# não é exatamente o que foi digitado
def listar_produtos(db: Session, limit: int, offset: int, publico: bool = False, busca: str | None = None,
                    ordem: str | None = None, **filtros) -> dict:
    if publico:
        filtros.update(ativo=True, categoria_ativa=True)
    palavras = palavras_da_busca(busca)
    ordem = _ordem_padrao(ordem, palavras)
    produtos, total = repo.listar_produtos(db, limit, offset, palavras=palavras or None, ordem=ordem, **filtros)
    alternativa = None
    if publico and palavras and total == 0:
        produtos, total = repo.listar_produtos(
            db, limit, offset, palavras=palavras, aproximada=True, ordem="relevancia", **filtros,
        )
        alternativa = "parecidas"
        if total == 0:
            produtos, total = repo.listar_produtos(db, limit, offset, ordem="novidades", **filtros)
            alternativa = "novidades" if total else None
    ids = [p.id_produto for p in produtos]
    variantes = repo.variantes_dos_produtos(db, ids)
    imagens = repo.imagens_dos_produtos(db, ids)
    disponiveis = _disponiveis(db, variantes)
    itens = [
        montar_produto(p, variantes[p.id_produto], publico, imagens[p.id_produto], disponiveis) for p in produtos
    ]
    return {"items": itens, "total": total, "limit": limit, "offset": offset, "busca_alternativa": alternativa}


# letras na ordem da grade (PP a XG), depois números em ordem crescente, depois o resto (ex.: U)
_GRADE = ["PP", "P", "M", "G", "GG", "XG"]


def _ordem_do_tamanho(tamanho: str) -> tuple:
    if tamanho.upper() in _GRADE:
        return (0, _GRADE.index(tamanho.upper()), "")
    if tamanho.isdigit():
        return (1, int(tamanho), "")
    return (2, 0, tamanho.lower())


def tamanhos_a_venda(db: Session, id_categoria: int | None = None, busca: str | None = None) -> list[str]:
    palavras = palavras_da_busca(busca) or None
    return sorted(repo.tamanhos_a_venda(db, id_categoria, palavras), key=_ordem_do_tamanho)


def buscar_produto(db: Session, id_produto: int, publico: bool = False) -> dict:
    produto = _produto_ou_404(db, id_produto)
    if publico and (not produto.ativo or not repo.buscar_categoria(db, produto.id_categoria).ativo):
        raise RecursoNaoEncontrado("Produto não encontrado")
    variantes = repo.variantes_dos_produtos(db, [id_produto])
    return montar_produto(
        produto, variantes[id_produto], publico, repo.imagens_dos_produtos(db, [id_produto])[id_produto],
        _disponiveis(db, variantes),
    )


# ---------- fotos ----------

# a cor da foto, quando informada, precisa ser de uma variante do produto (sem cor: vale para todas)
def _cor_do_produto(db: Session, id_produto: int, cor: str | None) -> str | None:
    if cor is None:
        return None
    for variante in repo.variantes_dos_produtos(db, [id_produto])[id_produto]:
        if variante.cor.lower() == cor.lower():
            return variante.cor
    raise RegraDeNegocio(f"O produto não tem variante na cor {cor}")


# o arquivo sobe antes da gravação (nenhuma chamada externa dentro de transação);
# sem ordem, a foto entra no fim
def adicionar_imagem(db: Session, storage, id_produto: int, arquivo, cor: str | None, ordem: int | None) -> dict:
    _produto_ou_404(db, id_produto)
    cor = _cor_do_produto(db, id_produto, cor)
    caminho = arquivos.enviar(storage, FOTO_PRODUTO, f"produto-{id_produto}", arquivo)
    imagem = ImagemProduto(
        id_produto=id_produto, cor=cor, caminho_arquivo=caminho,
        ordem=ordem or repo.proxima_ordem_de_imagem(db, id_produto),
    )
    try:
        db.add(imagem)
        db.commit()
    except Exception:
        db.rollback()
        arquivos.desfazer_envio(storage, FOTO_PRODUTO, caminho)
        raise
    db.refresh(imagem)
    return montar_imagem(imagem)


def alterar_imagem(db: Session, id_imagem: int, campos: dict) -> dict:
    imagem = repo.buscar_imagem(db, id_imagem)
    if imagem is None:
        raise RecursoNaoEncontrado("Foto não encontrada")
    if "cor" in campos:
        campos["cor"] = _cor_do_produto(db, imagem.id_produto, campos["cor"])
    for campo, valor in campos.items():
        setattr(imagem, campo, valor)
    db.commit()
    db.refresh(imagem)
    return montar_imagem(imagem)


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
        validar_local(db, variante.id_variante, carga["id_unidade"], carga["canal"])
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


# data sem hora vale o fim do dia em Brasília, como no histórico de estoque (case, seção 5)
def historico_preco(db: Session, id_variante: int, em: str | None) -> list[HistoricoPreco]:
    _variante_ou_404(db, id_variante)
    momento = interpretar_momento(em) if em else None
    return repo.historico_preco(db, id_variante, momento)
