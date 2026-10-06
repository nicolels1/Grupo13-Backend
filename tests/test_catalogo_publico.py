from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from src.app import app
from src.database.session import get_db
from src.middlewares.permissoes import get_usuario_opcional
from src.models.catalogo import CategoriaProduto, Produto, Variante
from src.repositories import catalogo_repository
from src.use_cases import catalogo
from src.use_cases.erros import RecursoNaoEncontrado
from tests.apoio import SessaoFalsa, funcionario

BRASILIA = ZoneInfo("America/Sao_Paulo")


def produto(id_produto=5, ativo=True, id_categoria=1):
    return Produto(id_produto=id_produto, id_categoria=id_categoria, nome="Camisa", descricao_tecnica="algodão 30.1",
                   descricao_cliente="macia", ativo=ativo)


def variante(id_variante, ativo=True):
    return Variante(id_variante=id_variante, id_produto=5, sku=f"CAM-{id_variante}", cor="Azul", tamanho="M",
                    preco=Decimal("59.90"), ativo=ativo)


@pytest.fixture
def banco(monkeypatch):
    estado = SimpleNamespace(
        produtos={5: produto()},
        variantes=[variante(50), variante(51, ativo=False)],
        categorias={1: CategoriaProduto(id_categoria=1, nome="Camisas", ativo=True),
                    2: CategoriaProduto(id_categoria=2, nome="Antigas", ativo=False)},
        filtros=None, historico_em="nao chamado",
    )

    def listar(db, limit, offset, **filtros):
        estado.filtros = filtros
        return list(estado.produtos.values()), len(estado.produtos)

    def historico(db, id_variante, em=None):
        estado.historico_em = em
        return []

    m = monkeypatch.setattr
    m(catalogo_repository, "listar_produtos", listar)
    m(catalogo_repository, "buscar_produto", lambda db, i: estado.produtos.get(i))
    m(catalogo_repository, "buscar_categoria", lambda db, i: estado.categorias.get(i))
    m(catalogo_repository, "buscar_variante", lambda db, i: next((v for v in estado.variantes if v.id_variante == i), None))
    m(catalogo_repository, "variantes_dos_produtos", lambda db, ids: {
        i: [v for v in estado.variantes if v.id_produto == i] for i in ids})
    m(catalogo_repository, "historico_preco", historico)
    return estado


# ---------- quem vê o quê ----------

def test_sem_login_e_visao_publica():
    assert catalogo.visao_publica(SessaoFalsa(), None) is True


@pytest.mark.parametrize("tem_permissao, publico", [(True, False), (False, True)])
def test_quem_gerencia_o_catalogo_ve_tudo(monkeypatch, tem_permissao, publico):
    monkeypatch.setattr(catalogo, "usuario_tem_permissao", lambda db, u, *c: tem_permissao)
    assert catalogo.visao_publica(SessaoFalsa(), funcionario()) is publico


def test_vitrine_so_lista_ativos_mesmo_pedindo_inativos(banco):
    resultado = catalogo.listar_produtos(SessaoFalsa(), 10, 0, publico=True, ativo=False)

    assert banco.filtros == {"ativo": True, "categoria_ativa": True}
    [item] = resultado["items"]
    assert item["descricao_tecnica"] is None
    assert [v.id_variante for v in item["variantes"]] == [50]  # a variante desativada some


def test_equipe_do_catalogo_ve_inativos_e_descricao_tecnica(banco):
    resultado = catalogo.listar_produtos(SessaoFalsa(), 10, 0, ativo=False)

    assert banco.filtros == {"ativo": False}
    [item] = resultado["items"]
    assert item["descricao_tecnica"] == "algodão 30.1"
    assert len(item["variantes"]) == 2


@pytest.mark.parametrize("campos", [dict(ativo=False), dict(id_categoria=2)])
def test_produto_desativado_ou_de_categoria_desativada_some_da_vitrine(banco, campos):
    banco.produtos[5] = produto(**campos)

    with pytest.raises(RecursoNaoEncontrado):
        catalogo.buscar_produto(SessaoFalsa(), 5, publico=True)

    assert catalogo.buscar_produto(SessaoFalsa(), 5)["ativo"] == campos.get("ativo", True)


# ---------- histórico de preço com data ----------

def test_data_sem_hora_no_historico_de_preco_vale_o_fim_do_dia(banco):
    catalogo.historico_preco(SessaoFalsa(), 50, "2026-09-22")

    assert banco.historico_em == datetime(2026, 9, 22, 23, 59, 59, 999999, tzinfo=BRASILIA)


def test_historico_de_preco_sem_data_traz_tudo(banco):
    catalogo.historico_preco(SessaoFalsa(), 50, None)

    assert banco.historico_em is None


# ---------- rotas ----------

@pytest.fixture
def client(banco):
    def montar(usuario=None):
        app.dependency_overrides[get_db] = lambda: SessaoFalsa()
        app.dependency_overrides[get_usuario_opcional] = lambda: usuario
        return TestClient(app)

    yield montar
    app.dependency_overrides.clear()


def test_vitrine_sem_login_nao_mostra_descricao_tecnica(client):
    [item] = client().get("/produtos").json()["items"]

    assert item["descricao_tecnica"] is None


def test_equipe_do_catalogo_ve_a_descricao_tecnica(client, monkeypatch):
    monkeypatch.setattr(catalogo, "usuario_tem_permissao", lambda db, u, *c: True)

    [item] = client(usuario=funcionario()).get("/produtos").json()["items"]

    assert item["descricao_tecnica"] == "algodão 30.1"


def test_token_invalido_na_vitrine_responde_401(banco):
    app.dependency_overrides[get_db] = lambda: SessaoFalsa()
    try:
        resposta = TestClient(app).get("/produtos", headers={"Authorization": "Bearer nao-e-um-jwt"})
    finally:
        app.dependency_overrides.clear()

    assert resposta.status_code == 401
