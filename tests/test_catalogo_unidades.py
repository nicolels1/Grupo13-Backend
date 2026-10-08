from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.entities.unidades import UnidadeCriar
from src.models.catalogo import CategoriaProduto
from src.models.estoque import Unidade
from src.repositories import catalogo_repository, unidade_repository
from src.use_cases import catalogo, unidades
from src.use_cases.erros import Conflito, RecursoNaoEncontrado, RegraDeNegocio
from tests.apoio import SessaoFalsa, api, funcionario  # noqa: F401 (api é fixture)

ENDERECO = dict(rua="Rua A", numero="10", bairro="Centro", cidade="São Paulo", uf="sp", cep="01000-000")


@pytest.fixture
def banco(monkeypatch):
    estado = SimpleNamespace(categorias={}, unidades={})

    def por_nome(colecao):
        return lambda db, nome: next((x for x in colecao.values() if x.nome.lower() == nome.lower()), None)

    monkeypatch.setattr(catalogo_repository, "buscar_categoria", lambda db, i: estado.categorias.get(i))
    monkeypatch.setattr(catalogo_repository, "categoria_por_nome", por_nome(estado.categorias))
    monkeypatch.setattr(catalogo_repository, "listar_categorias", lambda db, ativo=None: list(estado.categorias.values()))
    monkeypatch.setattr(unidade_repository, "buscar_unidade", lambda db, i: estado.unidades.get(i))
    monkeypatch.setattr(unidade_repository, "unidade_por_nome", por_nome(estado.unidades))
    monkeypatch.setattr(unidade_repository, "listar_unidades", lambda db, ativo=None, tipo=None: list(estado.unidades.values()))
    return estado


def categoria(id_categoria, nome, ativo=True):
    return CategoriaProduto(id_categoria=id_categoria, nome=nome, ativo=ativo)


def unidade(id_unidade, nome, tipo="loja", despacha_online=False):
    return Unidade(id_unidade=id_unidade, nome=nome, tipo=tipo, despacha_online=despacha_online, ativo=True,
                   complemento=None, **{**ENDERECO, "uf": "SP", "cep": "01000000"})


# ---------- categoria: regras ----------

def test_cria_categoria(banco):
    db = SessaoFalsa()

    criada = catalogo.criar_categoria(db, "Camisas")

    assert (criada.nome, criada.ativo, db.commits) == ("Camisas", True, 1)


def test_categoria_com_nome_repetido_sem_diferenciar_maiusculas(banco):
    banco.categorias[1] = categoria(1, "Camisas")

    with pytest.raises(Conflito, match="Categoria já existe"):
        catalogo.criar_categoria(SessaoFalsa(), "camisas")


def test_alterar_categoria_inexistente(banco):
    with pytest.raises(RecursoNaoEncontrado):
        catalogo.alterar_categoria(SessaoFalsa(), 9, {"nome": "X"})


def test_renomear_para_nome_de_outra_categoria(banco):
    banco.categorias[1] = categoria(1, "Camisas")
    banco.categorias[2] = categoria(2, "Calças")

    with pytest.raises(Conflito):
        catalogo.alterar_categoria(SessaoFalsa(), 2, {"nome": "CAMISAS"})


def test_desativar_e_manter_o_proprio_nome(banco):
    banco.categorias[1] = categoria(1, "Camisas")

    alterada = catalogo.alterar_categoria(SessaoFalsa(), 1, {"nome": "Camisas", "ativo": False})

    assert alterada.ativo is False


# ---------- unidade: regras e validação ----------

def test_unidade_normaliza_cep_e_uf():
    dados = UnidadeCriar(nome="Loja Centro", tipo="loja", **ENDERECO)

    assert (dados.cep, dados.uf) == ("01000000", "SP")


def test_cd_sempre_despacha_online():
    assert UnidadeCriar(nome="CD", tipo="cd", **ENDERECO).despacha_online is True


@pytest.mark.parametrize("cep", ["0100", "abc", "010000000"])
def test_cep_invalido(cep):
    with pytest.raises(ValidationError):
        UnidadeCriar(nome="Loja", tipo="loja", **{**ENDERECO, "cep": cep})


def test_cria_unidade(banco):
    dados = UnidadeCriar(nome="Loja Centro", tipo="loja", **ENDERECO).model_dump()

    criada = unidades.criar_unidade(SessaoFalsa(), dados)

    assert (criada.nome, criada.tipo, criada.ativo) == ("Loja Centro", "loja", True)


def test_unidade_com_nome_repetido(banco):
    banco.unidades[1] = unidade(1, "Loja Centro")
    dados = UnidadeCriar(nome="loja centro", tipo="loja", **ENDERECO).model_dump()

    with pytest.raises(Conflito):
        unidades.criar_unidade(SessaoFalsa(), dados)


def test_cd_nao_deixa_de_despachar(banco):
    banco.unidades[1] = unidade(1, "CD", tipo="cd", despacha_online=True)

    with pytest.raises(RegraDeNegocio, match="sempre despacha"):
        unidades.alterar_unidade(SessaoFalsa(), 1, {"despacha_online": False})


def test_buscar_unidade_inexistente(banco):
    with pytest.raises(RecursoNaoEncontrado):
        unidades.buscar_unidade(SessaoFalsa(), 9)


# ---------- rotas ----------

def test_listar_categorias_e_publico(api, banco):
    banco.categorias[1] = categoria(1, "Camisas")

    resposta = api(SessaoFalsa()).get("/categorias")

    assert resposta.status_code == 200
    assert resposta.json() == {"items": [{"id_categoria": 1, "nome": "Camisas", "ativo": True, "imagem_url": None}]}


def test_criar_categoria_sem_login_retorna_401(api, banco):
    assert api(SessaoFalsa()).post("/categorias", json={"nome": "Camisas"}).status_code == 401


def test_criar_categoria_sem_permissao_retorna_403(api, banco):
    cliente = api(SessaoFalsa(), usuario=funcionario(), permitido=False)

    assert cliente.post("/categorias", json={"nome": "Camisas"}).status_code == 403


def test_criar_categoria_com_permissao(api, banco):
    resposta = api(SessaoFalsa(), usuario=funcionario()).post("/categorias", json={"nome": "  Camisas "})

    assert resposta.status_code == 201
    assert resposta.json()["nome"] == "Camisas"


def test_categoria_repetida_retorna_409(api, banco):
    banco.categorias[1] = categoria(1, "Camisas")

    resposta = api(SessaoFalsa(), usuario=funcionario()).post("/categorias", json={"nome": "Camisas"})

    assert (resposta.status_code, resposta.json()) == (409, {"detail": "Categoria já existe"})


def test_patch_com_nome_nulo_nao_apaga_o_nome(api, banco):
    banco.categorias[1] = categoria(1, "Camisas")

    resposta = api(SessaoFalsa(), usuario=funcionario()).patch("/categorias/1", json={"nome": None, "ativo": False})

    assert resposta.json() == {"id_categoria": 1, "nome": "Camisas", "ativo": False, "imagem_url": None}


def test_criar_unidade_e_listar(api, banco):
    cliente = api(SessaoFalsa(), usuario=funcionario())

    criada = cliente.post("/unidades", json={"nome": "CD Norte", "tipo": "cd", **ENDERECO})

    assert criada.status_code == 201
    assert (criada.json()["despacha_online"], criada.json()["uf"], criada.json()["cep"]) == (True, "SP", "01000000")


def test_unidade_inexistente_retorna_404(api, banco):
    assert api(SessaoFalsa()).get("/unidades/9").status_code == 404


def test_patch_unidade_limpa_complemento(api, banco):
    banco.unidades[1] = unidade(1, "Loja Centro")
    banco.unidades[1].complemento = "Loja 2"

    resposta = api(SessaoFalsa(), usuario=funcionario()).patch("/unidades/1", json={"complemento": None})

    assert resposta.json()["complemento"] is None
