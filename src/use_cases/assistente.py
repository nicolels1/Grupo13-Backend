from datetime import datetime

from sqlalchemy.orm import Session

from src.config import settings
from src.models.estoque import Unidade
from src.repositories import assistente_repository as repo
from src.repositories import catalogo_repository
from src.use_cases import catalogo, horarios
from src.use_cases.erros import RecursoNaoEncontrado, RegraDeNegocio

# ferramentas do assistente do cliente (n8n), sem login: só o que a vitrine já mostra a qualquer pessoa.
# A IA escolhe a ferramenta e os parâmetros; o número, o link e o horário saem prontos daqui

MAXIMO_DE_PECAS = 5
# até aqui o assistente diz quantas peças sobraram e que está acabando; acima, só que tem
LIMITE_ACABANDO = 3


def link_do_produto(id_produto: int) -> str:
    return f"{settings.SITE_URL}/loja/produtos/{id_produto}"


def link_do_carrinho(id_produto: int, id_variante: int) -> str:
    return f"{settings.SITE_URL}/loja/carrinho/adicionar?produto={id_produto}&variante={id_variante}"


def faixa_de_estoque(quantidade: int) -> dict:
    acabando = 0 < quantidade <= LIMITE_ACABANDO
    return {"tem": quantidade > 0, "quantidade": quantidade if acabando else None, "acabando": acabando}


def endereco(unidade: Unidade) -> str:
    complemento = f" - {unidade.complemento}" if unidade.complemento else ""
    return f"{unidade.rua}, {unidade.numero}{complemento} - {unidade.bairro}, {unidade.cidade}/{unidade.uf}"


def _sem_repetir(valores) -> list:
    return list(dict.fromkeys(valores))


# o produto da vitrine resumido para a conversa: as cores e os tamanhos que dá para comprar no site
def montar_peca(produto: dict) -> dict:
    variantes = produto["variantes"]
    no_site = [v["tamanho"] for v in variantes if v["disponivel"]]
    return {
        "id_produto": produto["id_produto"],
        "nome": produto["nome"],
        "marca": produto["marca"],
        "descricao": produto["descricao_cliente"],
        "preco_a_partir": min(v["preco"] for v in variantes),
        "cores": _sem_repetir(v["cor"] for v in variantes),
        "tamanhos_no_site": sorted(_sem_repetir(no_site), key=catalogo._ordem_do_tamanho),
        "foto": produto["imagens"][0]["url"] if produto["imagens"] else None,
        "link": link_do_produto(produto["id_produto"]),
    }


# "azul" acha "Azul Marinho": todas as palavras da cor pedida, sem acento, aparecem na cor da peça
def tem_a_cor(peca: dict, cor: str) -> bool:
    pedidas = set(catalogo.palavras_da_busca(cor))
    return any(pedidas <= set(catalogo.palavras_da_busca(c)) for c in peca["cores"])


# a IA traduz "aquela camisa azul de botão" para os filtros. A busca da vitrine exige todas as palavras
# no mesmo produto, então a cor não entra nela (camisa de botão azul não acharia a camisa de botão
# que só existe em branco): busca mais peças e põe primeiro as da cor pedida. Sem resultado exato,
# a vitrine devolve as parecidas ou as novidades, e o assistente avisa que não é o que foi pedido
PECAS_PARA_ORDENAR = 20


def buscar_pecas(db: Session, descricao: str, cor: str | None = None, tamanho: str | None = None,
                 marca: str | None = None, categoria: str | None = None) -> dict:
    busca = " ".join(parte for parte in (descricao, categoria) if parte)
    resultado = catalogo.listar_produtos(
        db, PECAS_PARA_ORDENAR, 0, publico=True, busca=busca, tamanhos=[tamanho] if tamanho else None, marca=marca,
    )
    pecas = [montar_peca(p) for p in resultado["items"] if p["variantes"]]
    if cor:
        pecas.sort(key=lambda peca: not tem_a_cor(peca, cor))
    return {"items": pecas[:MAXIMO_DE_PECAS], "resultado": resultado["busca_alternativa"] or "exato"}


def _variante_pedida(produto: dict, cor: str, tamanho: str) -> dict:
    escolhida = next(
        (v for v in produto["variantes"]
         if v["cor"].lower() == cor.lower() and v["tamanho"].lower() == tamanho.lower()),
        None,
    )
    if escolhida is None:
        cores = ", ".join(_sem_repetir(v["cor"] for v in produto["variantes"]))
        tamanhos = ", ".join(_sem_repetir(v["tamanho"] for v in produto["variantes"]))
        raise RecursoNaoEncontrado(
            f"{produto['nome']} não existe na cor {cor} no tamanho {tamanho}. Cores: {cores}. Tamanhos: {tamanhos}."
        )
    return escolhida


def _no_site_e_nas_lojas(db: Session, id_variante: int) -> tuple[int, list[tuple[Unidade, int]]]:
    linhas = repo.disponivel_por_unidade(db, id_variante)
    no_site = sum(quantidade for _, canal, quantidade in linhas if canal == "online")
    nas_lojas = [(u, quantidade) for u, canal, quantidade in linhas if canal == "loja_fisica" and u.tipo == "loja"]
    return no_site, nas_lojas


# "tem a peça X no M?": no site (estoque online de todas as unidades) e em cada loja (vitrine física),
# já sem o reservado. A quantidade só aparece quando está acabando
def ver_estoque(db: Session, id_produto: int, cor: str, tamanho: str) -> dict:
    produto = catalogo.buscar_produto(db, id_produto, publico=True)
    variante = _variante_pedida(produto, cor, tamanho)
    no_site, nas_lojas = _no_site_e_nas_lojas(db, variante["id_variante"])
    return {
        "id_variante": variante["id_variante"],
        "produto": produto["nome"],
        "marca": produto["marca"],
        "cor": variante["cor"],
        "tamanho": variante["tamanho"],
        "preco": variante["preco"],
        "site": faixa_de_estoque(no_site),
        "lojas": [
            {"id_unidade": u.id_unidade, "nome": u.nome, "endereco": endereco(u), **faixa_de_estoque(quantidade)}
            for u, quantidade in nas_lojas
        ],
    }


# o link abre o site com a peça no carrinho; a IA nunca escreve o link. Sem estoque para o site, a
# resposta diz em quais lojas tem, para o assistente oferecer a loja
def link_carrinho(db: Session, id_variante: int) -> dict:
    variante = catalogo_repository.buscar_variante(db, id_variante)
    if variante is None or not variante.ativo:
        raise RecursoNaoEncontrado("Peça não encontrada")
    produto = catalogo.buscar_produto(db, variante.id_produto, publico=True)
    no_site, nas_lojas = _no_site_e_nas_lojas(db, id_variante)
    if no_site == 0:
        lojas = ", ".join(u.nome for u, _ in nas_lojas)
        onde = f" Tem na loja: {lojas}." if lojas else " Também não tem nas lojas."
        raise RegraDeNegocio(f"{produto['nome']} ({variante.cor}, {variante.tamanho}) está sem estoque para o site.{onde}")
    return {
        "id_variante": id_variante,
        "produto": produto["nome"],
        "cor": variante.cor,
        "tamanho": variante.tamanho,
        "link": link_do_carrinho(variante.id_produto, id_variante),
    }


# endereço e horário de cada loja, agrupadas por marca; o horário de hoje vem em texto pronto
def lojas(db: Session, agora: datetime) -> dict:
    unidades = repo.lojas_ativas(db)
    ids = [u.id_unidade for u in unidades]
    horarios_por_loja = repo.horarios_das_unidades(db, ids)
    excecoes = repo.excecoes_do_dia(db, ids, agora.astimezone(horarios.BRASILIA).date())
    grupos: dict[str, list[dict]] = {}
    for u in unidades:
        do_dia = horarios_por_loja.get(u.id_unidade, [])
        grupos.setdefault(u.marca, []).append({
            "id_unidade": u.id_unidade,
            "nome": u.nome,
            "endereco": endereco(u),
            "hoje": horarios.situacao_de_hoje(do_dia, excecoes.get(u.id_unidade), agora),
            "semana": horarios.semana(do_dia),
        })
    return {"items": [{"marca": marca, "lojas": lista} for marca, lista in grupos.items()]}


# as palavras do catálogo, para o prompt: o cliente fala "celeste" e a busca precisa de "Azul"
def vocabulario(db: Session) -> dict:
    return {
        "categorias": [c["nome"] for c in catalogo.listar_categorias(db, ativo=True, marca=None)],
        "cores": repo.cores_a_venda(db),
    }
