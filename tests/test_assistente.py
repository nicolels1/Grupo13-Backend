from datetime import date, datetime, time
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.app import app
from src.config import settings
from src.database.session import get_db
from src.models.catalogo import Variante
from src.models.estoque import Unidade
from src.models.horarios import ExcecaoHorario, HorarioUnidade
from src.repositories import assistente_repository, catalogo_repository
from src.use_cases import assistente, catalogo
from src.use_cases.erros import RecursoNaoEncontrado, RegraDeNegocio
from src.use_cases.horarios import BRASILIA
from tests.apoio import SessaoFalsa

CHAVE = "chave-de-teste"


def unidade(id_unidade, nome, tipo="loja", marca="lorenzi", complemento=None):
    return Unidade(id_unidade=id_unidade, nome=nome, tipo=tipo, marca=marca, rua="Rua A", numero="10",
                   complemento=complemento, bairro="Centro", cidade="São Paulo", uf="SP", cep="01000000", ativo=True)


PINHEIROS = unidade(2, "Loja Pinheiros")
ELDORADO = unidade(3, "Loja Eldorado", complemento="Shopping Eldorado")
CD = unidade(1, "CD Guarulhos", tipo="cd")


def variante_saida(id_variante, cor, tamanho, preco="99.90", disponivel=True):
    return {"id_variante": id_variante, "cor": cor, "tamanho": tamanho, "preco": Decimal(preco), "disponivel": disponivel}


def produto_saida(id_produto=5, variantes=None, imagens=()):
    return {
        "id_produto": id_produto, "nome": "Camisa Social", "marca": "lorenzi", "descricao_cliente": "Camisa de botão",
        "variantes": variantes if variantes is not None else [
            variante_saida(50, "Azul", "G", "129.90"),
            variante_saida(51, "Azul", "M", "119.90", disponivel=False),
            variante_saida(52, "Branca", "P", "119.90"),
        ],
        "imagens": list(imagens),
    }


@pytest.fixture
def banco(monkeypatch):
    estado = SimpleNamespace(produto=produto_saida(), estoque=[], busca=None, alternativa=None)

    def listar(db, limit, offset, publico=False, busca=None, **filtros):
        estado.busca = {"limit": limit, "publico": publico, "busca": busca, **filtros}
        return {"items": [estado.produto], "total": 1, "limit": limit, "offset": offset,
                "busca_alternativa": estado.alternativa}

    def buscar(db, id_produto, publico=False):
        if id_produto != estado.produto["id_produto"]:
            raise RecursoNaoEncontrado("Produto não encontrado")
        return estado.produto

    m = monkeypatch.setattr
    m(settings, "SITE_URL", "https://loja.exemplo")
    m(catalogo, "listar_produtos", listar)
    m(catalogo, "buscar_produto", buscar)
    m(assistente_repository, "disponivel_por_unidade", lambda db, id_variante: estado.estoque)
    return estado


# ---------- buscar peças ----------

def test_busca_pela_descricao_e_categoria_sem_a_cor(banco):
    assistente.buscar_pecas(SessaoFalsa(), "camisa de botão", cor="azul", tamanho="M", marca="lorenzi", categoria="Camisas")

    assert banco.busca == {"limit": 20, "publico": True, "busca": "camisa de botão Camisas",
                           "tamanhos": ["M"], "marca": "lorenzi"}


def test_pecas_da_cor_pedida_vem_primeiro_e_limita_a_cinco(monkeypatch, banco):
    def peca(id_produto, cor):
        return produto_saida(id_produto, variantes=[variante_saida(id_produto * 10, cor, "M")])

    lista = [peca(1, "Branca"), peca(2, "Azul Marinho"), peca(3, "Preta"), peca(4, "Verde"), peca(5, "Bege"),
             peca(6, "azul")]
    monkeypatch.setattr(catalogo, "listar_produtos", lambda db, *a, **k: {"items": lista, "busca_alternativa": None})

    resposta = assistente.buscar_pecas(SessaoFalsa(), "camisa", cor="Azul")

    assert [p["id_produto"] for p in resposta["items"]] == [2, 6, 1, 3, 4]


@pytest.mark.parametrize("cor, tem", [("azul", True), ("AZUL marinho", True), ("lilas", True), ("verde", False)])
def test_cor_pedida_casa_por_palavra_sem_acento(cor, tem):
    assert assistente.tem_a_cor({"cores": ["Azul Marinho", "Lilás"]}, cor) is tem


def test_peca_resumida_para_a_conversa(banco):
    banco.produto = produto_saida(imagens=[{"url": "https://foto/1.jpg"}])

    resposta = assistente.buscar_pecas(SessaoFalsa(), "camisa")

    assert resposta["resultado"] == "exato"
    [peca] = resposta["items"]
    assert peca["preco_a_partir"] == Decimal("119.90")
    assert peca["cores"] == ["Azul", "Branca"]
    assert peca["tamanhos_no_site"] == ["P", "G"]  # M não tem para o site; na ordem da grade
    assert peca["foto"] == "https://foto/1.jpg"
    assert peca["link"] == "https://loja.exemplo/loja/produtos/5"


def test_busca_sem_resultado_exato_avisa(banco):
    banco.alternativa = "parecidas"
    assert assistente.buscar_pecas(SessaoFalsa(), "camisa")["resultado"] == "parecidas"


def test_produto_sem_variante_ativa_fica_de_fora(banco):
    banco.produto = produto_saida(variantes=[])
    assert assistente.buscar_pecas(SessaoFalsa(), "camisa")["items"] == []


# ---------- estoque ----------

@pytest.mark.parametrize("quantidade, esperado", [
    (0, {"tem": False, "quantidade": None, "acabando": False}),
    (1, {"tem": True, "quantidade": 1, "acabando": True}),
    (3, {"tem": True, "quantidade": 3, "acabando": True}),
    (4, {"tem": True, "quantidade": None, "acabando": False}),
])
def test_quantidade_so_aparece_quando_esta_acabando(quantidade, esperado):
    assert assistente.faixa_de_estoque(quantidade) == esperado


def test_estoque_soma_o_site_e_lista_as_lojas(banco):
    banco.estoque = [(CD, "online", 7), (PINHEIROS, "online", 2), (PINHEIROS, "loja_fisica", 2), (ELDORADO, "loja_fisica", 9)]

    resposta = assistente.ver_estoque(SessaoFalsa(), 5, "azul", "g")

    assert (resposta["id_variante"], resposta["cor"], resposta["tamanho"]) == (50, "Azul", "G")
    assert resposta["site"] == {"tem": True, "quantidade": None, "acabando": False}  # 7 + 2
    assert [(l["nome"], l["quantidade"], l["acabando"]) for l in resposta["lojas"]] == [
        ("Loja Pinheiros", 2, True), ("Loja Eldorado", None, False),
    ]
    assert resposta["lojas"][1]["endereco"] == "Rua A, 10 - Shopping Eldorado - Centro, São Paulo/SP"


def test_vitrine_fisica_do_cd_nao_conta_como_loja(banco):
    banco.estoque = [(CD, "loja_fisica", 5)]
    assert assistente.ver_estoque(SessaoFalsa(), 5, "Azul", "G")["lojas"] == []


def test_cor_ou_tamanho_que_nao_existe_traz_as_opcoes(banco):
    with pytest.raises(RecursoNaoEncontrado, match="Cores: Azul, Branca. Tamanhos: G, M, P"):
        assistente.ver_estoque(SessaoFalsa(), 5, "Verde", "G")


# ---------- link do carrinho ----------

@pytest.fixture
def variante_50(monkeypatch):
    peca = Variante(id_variante=50, id_produto=5, cor="Azul", tamanho="G", ativo=True)
    monkeypatch.setattr(catalogo_repository, "buscar_variante", lambda db, i: peca if i == 50 else None)
    return peca


def test_link_so_com_estoque_para_o_site(banco, variante_50):
    banco.estoque = [(CD, "online", 1)]

    resposta = assistente.link_carrinho(SessaoFalsa(), 50)

    assert resposta["link"] == "https://loja.exemplo/loja/carrinho/adicionar?produto=5&variante=50"


def test_sem_estoque_para_o_site_diz_as_lojas_que_tem(banco, variante_50):
    banco.estoque = [(PINHEIROS, "loja_fisica", 2)]
    with pytest.raises(RegraDeNegocio, match="sem estoque para o site. Tem na loja: Loja Pinheiros."):
        assistente.link_carrinho(SessaoFalsa(), 50)


def test_sem_estoque_em_lugar_nenhum(banco, variante_50):
    with pytest.raises(RegraDeNegocio, match="Também não tem nas lojas."):
        assistente.link_carrinho(SessaoFalsa(), 50)


def test_link_de_peca_desativada_ou_inexistente(banco, variante_50):
    variante_50.ativo = False
    for id_variante in (50, 99):
        with pytest.raises(RecursoNaoEncontrado):
            assistente.link_carrinho(SessaoFalsa(), id_variante)


# ---------- lojas e vocabulário ----------

def test_lojas_agrupadas_por_marca_com_o_horario_de_hoje(monkeypatch):
    vulto = unidade(4, "Vulto Savassi", marca="vulto")
    pedido = {}

    def excecoes(db, ids, dia):
        pedido["dia"] = dia
        return {2: ExcecaoHorario(id_unidade=2, data=dia, abre=None, fecha=None, motivo="reforma")}

    m = monkeypatch.setattr
    m(assistente_repository, "lojas_ativas", lambda db: [PINHEIROS, ELDORADO, vulto])
    m(assistente_repository, "horarios_das_unidades", lambda db, ids: {
        3: [HorarioUnidade(id_unidade=3, dia_semana=2, abre=time(10), fecha=time(22))]})
    m(assistente_repository, "excecoes_do_dia", excecoes)

    # quarta 28/10, 15h
    resposta = assistente.lojas(SessaoFalsa(), datetime(2026, 10, 28, 15, tzinfo=BRASILIA))

    assert pedido["dia"] == date(2026, 10, 28)
    lorenzi, da_vulto = resposta["items"]
    assert [l["nome"] for l in lorenzi["lojas"]] == ["Loja Pinheiros", "Loja Eldorado"]
    assert da_vulto["marca"] == "vulto"
    assert lorenzi["lojas"][0]["hoje"]["texto"] == "Hoje (reforma) a loja fica fechada."
    assert lorenzi["lojas"][1]["hoje"] == {"aberta_agora": True, "texto": "Hoje a loja está aberta até as 22h."}
    assert da_vulto["lojas"][0]["hoje"]["texto"] == "Hoje a loja fica fechada."  # sem horário cadastrado


def test_vocabulario_traz_categorias_ativas_e_cores(monkeypatch):
    pedido = {}

    def categorias(db, ativo, marca):
        pedido["ativo"] = ativo
        return [{"nome": "Camisas"}, {"nome": "Calças"}]

    monkeypatch.setattr(catalogo, "listar_categorias", categorias)
    monkeypatch.setattr(assistente_repository, "cores_a_venda", lambda db: ["Azul", "Preta"])

    assert assistente.vocabulario(SessaoFalsa()) == {"categorias": ["Camisas", "Calças"], "cores": ["Azul", "Preta"]}
    assert pedido["ativo"] is True


# ---------- a chave das rotas ----------

@pytest.fixture
def cliente(monkeypatch, banco):
    monkeypatch.setattr(settings, "ASSISTENTE_CHAVE", CHAVE)
    app.dependency_overrides[get_db] = lambda: SessaoFalsa()
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.mark.parametrize("cabecalho", [{}, {"X-Assistente-Chave": "errada"}])
def test_rota_sem_a_chave_certa_e_recusada(cliente, cabecalho):
    resposta = cliente.get("/assistente/pecas", params={"descricao": "camisa"}, headers=cabecalho)
    assert resposta.status_code == 401


def test_sem_chave_configurada_as_rotas_ficam_fechadas(cliente, monkeypatch):
    monkeypatch.setattr(settings, "ASSISTENTE_CHAVE", None)
    resposta = cliente.get("/assistente/pecas", params={"descricao": "camisa"}, headers={"X-Assistente-Chave": ""})
    assert resposta.status_code == 503


def test_rota_com_a_chave_responde(cliente):
    resposta = cliente.get("/assistente/pecas", params={"descricao": "camisa"}, headers={"X-Assistente-Chave": CHAVE})

    assert resposta.status_code == 200
    assert resposta.json()["items"][0]["link"] == "https://loja.exemplo/loja/produtos/5"


def test_sem_estoque_para_o_site_responde_422_com_as_lojas(cliente, banco, variante_50):
    banco.estoque = [(PINHEIROS, "loja_fisica", 1)]

    resposta = cliente.get("/assistente/link-carrinho", params={"id_variante": 50}, headers={"X-Assistente-Chave": CHAVE})

    assert resposta.status_code == 422
    assert "Loja Pinheiros" in resposta.json()["detail"]
