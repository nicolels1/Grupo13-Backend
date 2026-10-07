from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.entities.catalogo import ProdutoCriar, VarianteCriar
from src.models.catalogo import CategoriaProduto, HistoricoPreco, Produto, Variante
from src.models.estoque import MovimentacaoEstoque
from src.repositories import estoque_repository
from src.repositories import catalogo_repository
from src.use_cases import catalogo
from pydantic import ValidationError

from src.use_cases.erros import Conflito, RecursoNaoEncontrado, RegraDeNegocio
from tests.apoio import ID_FUNCIONARIO, SessaoFalsa, api, funcionario  # noqa: F401 (api é fixture)


@pytest.fixture
def banco(monkeypatch):
    estado = SimpleNamespace(
        categorias={1: CategoriaProduto(id_categoria=1, nome="Camisas", ativo=True),
                    2: CategoriaProduto(id_categoria=2, nome="Antigas", ativo=False)},
        produtos={},
        variantes={},
        historico=[],
    )
    r = catalogo_repository
    monkeypatch.setattr(r, "buscar_categoria", lambda db, i: estado.categorias.get(i))
    monkeypatch.setattr(r, "buscar_produto", lambda db, i: estado.produtos.get(i))
    monkeypatch.setattr(r, "listar_produtos", lambda db, limit, offset, **f: (list(estado.produtos.values()), len(estado.produtos)))
    monkeypatch.setattr(r, "variantes_dos_produtos", lambda db, ids: {
        i: [v for v in estado.variantes.values() if v.id_produto == i] for i in ids
    })
    monkeypatch.setattr(r, "buscar_variante", lambda db, i: estado.variantes.get(i))
    monkeypatch.setattr(r, "imagens_dos_produtos", lambda db, ids: {i: [] for i in ids})
    monkeypatch.setattr(r, "variante_por_sku", lambda db, sku: next(
        (v for v in estado.variantes.values() if v.sku.upper() == sku.upper()), None))
    monkeypatch.setattr(r, "variante_por_cor_e_tamanho", lambda db, p, cor, tam: next(
        (v for v in estado.variantes.values()
         if v.id_produto == p and v.cor.lower() == cor.lower() and v.tamanho.lower() == tam.lower()), None))
    monkeypatch.setattr(r, "historico_preco", lambda db, v, em=None: [h for h in estado.historico if h.id_variante == v])
    return estado


def com_produto(banco):
    banco.produtos[5] = Produto(id_produto=5, id_categoria=1, nome="Camisa", descricao_tecnica="t",
                                descricao_cliente="c", ativo=True)
    banco.variantes[50] = Variante(id_variante=50, id_produto=5, sku="CAM-AZ-M", cor="Azul", tamanho="M",
                                   preco=Decimal("59.90"), ativo=True)


def novo_produto(**kw):
    dados = dict(id_categoria=1, nome="Camisa", descricao_tecnica="algodão", descricao_cliente="macia",
                 variantes=[{"sku": "cam-az-m", "cor": "Azul", "tamanho": "M", "preco": "59.90"},
                            {"sku": "cam-az-g", "cor": "Azul", "tamanho": "G", "preco": "59.90"}])
    dados.update(kw)
    return ProdutoCriar(**dados).model_dump()


def historicos(db):
    return [o for o in db.adicionados if isinstance(o, HistoricoPreco)]


# ---------- criação ----------

def test_cria_produto_com_variantes_e_historico_de_preco(banco):
    db = SessaoFalsa()

    produto = catalogo.criar_produto(db, ID_FUNCIONARIO, novo_produto())

    assert [v.sku for v in produto["variantes"]] == ["CAM-AZ-M", "CAM-AZ-G"]
    registros = historicos(db)
    assert len(registros) == 2
    assert all(h.preco_anterior is None and h.preco_novo == Decimal("59.90") for h in registros)
    assert all(h.id_alterado_por == ID_FUNCIONARIO for h in registros)
    assert db.commits == 1


def test_variante_sem_cor_e_tamanho_usa_unica_e_u():
    variante = VarianteCriar(sku="BOLSA-1", preco="120")

    assert (variante.cor, variante.tamanho) == ("Única", "U")


def test_categoria_desativada_nao_recebe_produto(banco):
    with pytest.raises(RegraDeNegocio, match="Categoria desativada"):
        catalogo.criar_produto(SessaoFalsa(), ID_FUNCIONARIO, novo_produto(id_categoria=2))


def test_categoria_inexistente(banco):
    with pytest.raises(RecursoNaoEncontrado, match="Categoria"):
        catalogo.criar_produto(SessaoFalsa(), ID_FUNCIONARIO, novo_produto(id_categoria=9))


def test_sku_repetido_na_mesma_lista(banco):
    variantes = [{"sku": "X", "cor": "Azul", "tamanho": "M", "preco": "1"},
                 {"sku": "x", "cor": "Azul", "tamanho": "G", "preco": "1"}]

    with pytest.raises(Conflito, match="SKU repetido"):
        catalogo.criar_produto(SessaoFalsa(), ID_FUNCIONARIO, novo_produto(variantes=variantes))


def test_cor_e_tamanho_repetidos_na_mesma_lista(banco):
    variantes = [{"sku": "A", "cor": "Azul", "tamanho": "M", "preco": "1"},
                 {"sku": "B", "cor": "azul", "tamanho": "m", "preco": "1"}]

    with pytest.raises(Conflito, match="Cor e tamanho repetidos"):
        catalogo.criar_produto(SessaoFalsa(), ID_FUNCIONARIO, novo_produto(variantes=variantes))


def test_sku_que_ja_existe_no_banco(banco):
    com_produto(banco)

    with pytest.raises(Conflito, match="CAM-AZ-M já existe"):
        catalogo.criar_produto(SessaoFalsa(), ID_FUNCIONARIO, novo_produto())


def test_adicionar_variante_com_cor_e_tamanho_existentes(banco):
    com_produto(banco)
    dados = VarianteCriar(sku="NOVO", cor="AZUL", tamanho="m", preco="10").model_dump()

    with pytest.raises(Conflito, match="já existe neste produto"):
        catalogo.adicionar_variante(SessaoFalsa(), 5, ID_FUNCIONARIO, dados)


# ---------- alteração e histórico ----------

def test_mudar_preco_grava_historico(banco):
    com_produto(banco)
    db = SessaoFalsa()

    variante = catalogo.alterar_variante(db, 50, ID_FUNCIONARIO, {"preco": Decimal("49.90")})

    assert variante.preco == Decimal("49.90")
    [registro] = historicos(db)
    assert (registro.preco_anterior, registro.preco_novo) == (Decimal("59.90"), Decimal("49.90"))


def test_mesmo_preco_nao_grava_historico(banco):
    com_produto(banco)
    db = SessaoFalsa()

    catalogo.alterar_variante(db, 50, ID_FUNCIONARIO, {"preco": Decimal("59.90"), "ativo": False})

    assert historicos(db) == []


def test_trocar_sku_para_um_que_ja_existe(banco):
    com_produto(banco)
    banco.variantes[51] = Variante(id_variante=51, id_produto=5, sku="CAM-AZ-G", cor="Azul", tamanho="G",
                                   preco=Decimal("1"), ativo=True)

    with pytest.raises(Conflito):
        catalogo.alterar_variante(SessaoFalsa(), 51, ID_FUNCIONARIO, {"sku": "CAM-AZ-M"})


def test_mover_produto_para_categoria_desativada(banco):
    com_produto(banco)

    with pytest.raises(RegraDeNegocio):
        catalogo.alterar_produto(SessaoFalsa(), 5, {"id_categoria": 2})


def test_historico_de_variante_inexistente(banco):
    with pytest.raises(RecursoNaoEncontrado):
        catalogo.historico_preco(SessaoFalsa(), 99, None)


# ---------- rotas ----------

def test_listar_produtos_e_publico_e_traz_variantes(api, banco):
    com_produto(banco)

    resposta = api(SessaoFalsa()).get("/produtos", params={"limit": 10})

    assert resposta.status_code == 200
    assert (resposta.json()["total"], resposta.json()["limit"], resposta.json()["offset"]) == (1, 10, 0)
    [produto] = resposta.json()["items"]
    assert produto["variantes"][0]["sku"] == "CAM-AZ-M"
    assert produto["variantes"][0]["preco"] == "59.90"


def test_criar_produto_pela_rota(api, banco):
    corpo = {"id_categoria": 1, "nome": "Camisa", "descricao_tecnica": "t", "descricao_cliente": "c",
             "variantes": [{"sku": "c1", "preco": 10}]}

    resposta = api(SessaoFalsa(), usuario=funcionario()).post("/produtos", json=corpo)

    assert resposta.status_code == 201
    assert resposta.json()["variantes"][0]["cor"] == "Única"


def test_preco_negativo_e_recusado(api, banco):
    com_produto(banco)

    resposta = api(SessaoFalsa(), usuario=funcionario()).patch("/variantes/50", json={"preco": -1})

    assert resposta.status_code == 422


def test_produto_inexistente_retorna_404(api, banco):
    assert api(SessaoFalsa()).get("/produtos/9").status_code == 404


def test_historico_exige_permissao(api, banco):
    com_produto(banco)

    assert api(SessaoFalsa()).get("/variantes/50/historico-preco").status_code == 401


def test_limite_da_pagina(api, banco):
    assert api(SessaoFalsa()).get("/produtos", params={"limit": 201}).status_code == 422


# ---------- estoque inicial ----------

@pytest.fixture
def unidades(monkeypatch):
    lojas = {1: SimpleNamespace(tipo="loja", ativo=True), 2: SimpleNamespace(tipo="cd", ativo=True)}
    monkeypatch.setattr(estoque_repository, "buscar_unidade", lambda db, u: lojas.get(u))
    monkeypatch.setattr(estoque_repository, "buscar_variante", lambda db, v: object())
    return lojas


def movimentacoes(db):
    return [(m.tipo, m.id_unidade, m.canal, m.quantidade) for m in db.adicionados if isinstance(m, MovimentacaoEstoque)]


def test_estoque_inicial_vira_saldo_inicial(banco, unidades):
    variantes = [{"sku": "A", "preco": "1", "estoque_inicial": [
        {"id_unidade": 1, "canal": "loja_fisica", "quantidade": 3}, {"id_unidade": 2, "canal": "online", "quantidade": 5}]}]
    db = SessaoFalsa()

    catalogo.criar_produto(db, ID_FUNCIONARIO, novo_produto(variantes=variantes))

    assert movimentacoes(db) == [("saldo_inicial", 1, "loja_fisica", 3), ("saldo_inicial", 2, "online", 5)]


def test_estoque_inicial_no_cd_so_online(banco, unidades):
    variantes = [{"sku": "A", "preco": "1", "estoque_inicial": [{"id_unidade": 2, "canal": "loja_fisica", "quantidade": 1}]}]

    with pytest.raises(RegraDeNegocio, match="CD"):
        catalogo.criar_produto(SessaoFalsa(), ID_FUNCIONARIO, novo_produto(variantes=variantes))


def test_estoque_inicial_repetido():
    with pytest.raises(ValidationError, match="repetido"):
        VarianteCriar(sku="A", preco="1", estoque_inicial=[
            {"id_unidade": 1, "canal": "online", "quantidade": 1}, {"id_unidade": 1, "canal": "online", "quantidade": 2}])
