from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

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
        filtros=None, historico_em="nao chamado", com_peca={50},
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
    m(catalogo_repository, "imagens_dos_produtos", lambda db, ids: {i: [] for i in ids})
    m(catalogo_repository, "ids_disponiveis_online", lambda db, ids: {i for i in ids if i in estado.com_peca})
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

    assert banco.filtros == {"ativo": True, "categoria_ativa": True, "palavras": None, "ordem": "nome"}
    [item] = resultado["items"]
    assert item["descricao_tecnica"] is None
    assert [v["id_variante"] for v in item["variantes"]] == [50]  # a variante desativada some


def test_equipe_do_catalogo_ve_inativos_e_descricao_tecnica(banco):
    resultado = catalogo.listar_produtos(SessaoFalsa(), 10, 0, ativo=False)

    assert banco.filtros == {"ativo": False, "palavras": None, "ordem": "nome"}
    [item] = resultado["items"]
    assert item["descricao_tecnica"] == "algodão 30.1"
    assert len(item["variantes"]) == 2


@pytest.mark.parametrize("campos", [dict(ativo=False), dict(id_categoria=2)])
def test_produto_desativado_ou_de_categoria_desativada_some_da_vitrine(banco, campos):
    banco.produtos[5] = produto(**campos)

    with pytest.raises(RecursoNaoEncontrado):
        catalogo.buscar_produto(SessaoFalsa(), 5, publico=True)

    assert catalogo.buscar_produto(SessaoFalsa(), 5)["ativo"] == campos.get("ativo", True)


# ---------- disponível online ----------

def test_variante_diz_se_tem_peca_online(banco):
    banco.variantes.append(variante(52))

    [item] = catalogo.listar_produtos(SessaoFalsa(), 10, 0, publico=True)["items"]
    detalhe = catalogo.buscar_produto(SessaoFalsa(), 5, publico=True)

    for produto_montado in (item, detalhe):
        assert {v["id_variante"]: v["disponivel"] for v in produto_montado["variantes"]} == {50: True, 52: False}


def test_variante_sem_consulta_de_estoque_fica_sem_disponivel():
    assert catalogo.montar_variante(variante(50))["disponivel"] is None


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


def test_rota_repassa_tamanho_disponivel_e_ordem(client, banco):
    resposta = client().get("/produtos", params={"tamanho": "M", "disponivel": "true", "ordem": "menor_preco"})

    assert resposta.status_code == 200
    assert resposta.json()["items"][0]["variantes"][0]["disponivel"] is True
    assert banco.filtros["tamanhos"] == ["M"]
    assert banco.filtros["disponivel"] is True
    assert banco.filtros["ordem"] == "menor_preco"


def test_lista_sem_ordem_continua_por_nome(client, banco):
    client().get("/produtos")

    assert banco.filtros["ordem"] == "nome"


def test_ordem_desconhecida_responde_422(client):
    assert client().get("/produtos", params={"ordem": "aleatoria"}).status_code == 422


def test_token_invalido_na_vitrine_responde_401(banco):
    app.dependency_overrides[get_db] = lambda: SessaoFalsa()
    try:
        resposta = TestClient(app).get("/produtos", headers={"Authorization": "Bearer nao-e-um-jwt"})
    finally:
        app.dependency_overrides.clear()

    assert resposta.status_code == 401


# ---------- consultas: o SQL monta para o PostgreSQL ----------

class SessaoQueGuarda:
    """Guarda as consultas que o repository mandaria ao banco e devolve resultados vazios."""

    def __init__(self):
        self.consultas = []

    def scalar(self, consulta):
        self.consultas.append(consulta)
        return 0

    def scalars(self, consulta):
        self.consultas.append(consulta)
        return []

    def execute(self, consulta):
        self.consultas.append(consulta)
        return []


def sql(consulta):
    return str(consulta.compile(dialect=postgresql.dialect()))


@pytest.mark.parametrize("ordem", ["nome", "novidades", "menor_preco", "maior_preco"])
def test_lista_de_produtos_monta_com_filtros_e_cada_ordem(ordem):
    db = SessaoQueGuarda()

    catalogo_repository.listar_produtos(db, 12, 0, tamanhos=["M"], disponivel=True, ordem=ordem)

    contagem, pagina = (sql(c) for c in db.consultas)
    assert "estoque.canal" in contagem and "variante.tamanho" in contagem
    assert "ORDER BY" in pagina


def test_disponivel_falso_lista_quem_nao_tem_peca_online():
    db = SessaoQueGuarda()

    catalogo_repository.listar_produtos(db, 12, 0, disponivel=False)

    assert "NOT IN" in sql(db.consultas[1])


def test_disponivel_online_desconta_o_reservado_e_ignora_a_vitrine_fisica():
    db = SessaoQueGuarda()

    catalogo_repository.ids_disponiveis_online(db, [50, 51])

    consulta = sql(db.consultas[0])
    assert "estoque.quantidade - estoque.quantidade_reservada" in consulta
    assert "estoque.canal = %(canal_1)s" in consulta
    assert db.consultas[0].compile().params["canal_1"] == "online"


def test_disponivel_online_sem_variantes_nao_consulta():
    db = SessaoQueGuarda()

    assert catalogo_repository.ids_disponiveis_online(db, []) == set()
    assert db.consultas == []


# ---------- tamanhos à venda ----------

def test_tamanhos_saem_na_ordem_da_grade(monkeypatch):
    monkeypatch.setattr(catalogo_repository, "tamanhos_a_venda",
                        lambda db, c, b: ["U", "42", "G", "38", "PP", "M", "Único"])

    assert catalogo.tamanhos_a_venda(SessaoFalsa()) == ["PP", "M", "G", "38", "42", "U", "Único"]


def test_rota_de_tamanhos_nao_e_lida_como_produto(client, monkeypatch):
    pedidos = []
    monkeypatch.setattr(catalogo_repository, "tamanhos_a_venda",
                        lambda db, c, b: pedidos.append((c, b)) or ["M", "P"])

    resposta = client().get("/produtos/tamanhos", params={"id_categoria": 23, "busca": "camisa"})

    assert resposta.status_code == 200
    assert resposta.json() == {"items": ["P", "M"]}
    assert pedidos == [(23, ["camisa"])]


def test_tamanhos_a_venda_so_contam_o_que_esta_ativo():
    db = SessaoQueGuarda()

    catalogo_repository.tamanhos_a_venda(db, id_categoria=23, palavras=["cam"])

    texto = sql(db.consultas[0])
    assert texto.startswith("SELECT DISTINCT variante.tamanho")
    assert "variante.ativo" in texto and "produto.ativo" in texto and "categoria_produto.ativo" in texto


# ---------- vários tamanhos no filtro ----------

def test_rota_aceita_varios_tamanhos(client, banco):
    resposta = client().get("/produtos", params=[("tamanho", "P"), ("tamanho", "M")])

    assert resposta.status_code == 200
    assert banco.filtros["tamanhos"] == ["P", "M"]


def test_sem_tamanho_nao_filtra(client, banco):
    client().get("/produtos")

    assert banco.filtros["tamanhos"] is None


def test_varios_tamanhos_viram_um_in_na_consulta():
    db = SessaoQueGuarda()

    catalogo_repository.listar_produtos(db, 12, 0, tamanhos=["P", "M"], disponivel=True)

    contagem = db.consultas[0].compile(dialect=postgresql.dialect())
    texto = str(contagem)
    assert "variante.tamanho IN (__[POSTCOMPILE_tamanho_1])" in texto
    assert contagem.params["tamanho_1"] == ["P", "M"]


# ---------- busca flexível ----------

@pytest.mark.parametrize("busca, palavras", [
    ("Calça  Jeans!", ["calca", "jeans"]),
    ("CAMISETA camiseta", ["camiseta"]),
    ("  ", []),
    (None, []),
    ("a b c d e f g h", ["a", "b", "c", "d", "e", "f"]),
])
def test_busca_vira_palavras_sem_acento(busca, palavras):
    assert catalogo.palavras_da_busca(busca) == palavras


def test_com_busca_e_sem_ordem_os_mais_parecidos_vem_primeiro(banco):
    catalogo.listar_produtos(SessaoFalsa(), 10, 0, publico=True, busca="calça")

    assert banco.filtros["palavras"] == ["calca"]
    assert banco.filtros["ordem"] == "relevancia"


def test_ordem_escolhida_vale_mesmo_com_busca(banco):
    catalogo.listar_produtos(SessaoFalsa(), 10, 0, publico=True, busca="calça", ordem="menor_preco")

    assert banco.filtros["ordem"] == "menor_preco"


@pytest.fixture
def busca_vazia(banco, monkeypatch):
    """Repository de mentira: a busca normal não acha nada; o que acontece depois depende do teste."""
    estado = SimpleNamespace(chamadas=[], acha_parecidas=True)

    def listar(db, limit, offset, **filtros):
        estado.chamadas.append(filtros)
        achou = filtros.get("aproximada") and estado.acha_parecidas or filtros.get("ordem") == "novidades"
        return (list(banco.produtos.values()), len(banco.produtos)) if achou else ([], 0)

    monkeypatch.setattr(catalogo_repository, "listar_produtos", listar)
    return estado


def test_vitrine_sem_resultado_traz_pecas_parecidas_e_avisa(busca_vazia):
    resultado = catalogo.listar_produtos(SessaoFalsa(), 10, 0, publico=True, busca="calssa")

    assert resultado["busca_alternativa"] == "parecidas" and resultado["total"] == 1
    assert busca_vazia.chamadas[1]["aproximada"] is True and busca_vazia.chamadas[1]["ordem"] == "relevancia"


def test_sem_nada_parecido_mostra_as_novidades(busca_vazia):
    busca_vazia.acha_parecidas = False

    resultado = catalogo.listar_produtos(SessaoFalsa(), 10, 0, publico=True, busca="xablau")

    assert resultado["busca_alternativa"] == "novidades" and resultado["total"] == 1
    assert "palavras" not in busca_vazia.chamadas[2] and busca_vazia.chamadas[2]["ordem"] == "novidades"


def test_catalogo_interno_nao_troca_a_busca_por_outra_coisa(busca_vazia):
    resultado = catalogo.listar_produtos(SessaoFalsa(), 10, 0, publico=False, busca="xablau")

    assert resultado["busca_alternativa"] is None and resultado["total"] == 0
    assert len(busca_vazia.chamadas) == 1


def test_rota_devolve_o_aviso_de_busca_alternativa(client):
    assert client().get("/produtos").json()["busca_alternativa"] is None


def test_busca_procura_no_texto_do_produto_sem_acento():
    db = SessaoQueGuarda()

    catalogo_repository.listar_produtos(db, 12, 0, palavras=["ca", "camisa"], ordem="relevancia")

    contagem, pagina = (sql(c) for c in db.consultas)
    # nome, categoria, cores e descrição juntos, sem acento
    assert "texto_de_busca(concat_ws(" in contagem and "produto.descricao_cliente" in contagem
    assert "string_agg(variante.cor" in contagem and "categoria_produto.nome" in contagem
    # "ca" só pelo começo da palavra (curta demais para semelhança); "camisa" também por semelhança
    assert contagem.count(" ~ ") == 2 and contagem.count("semelhanca_de_palavra(") == 1
    assert "ORDER BY" in pagina and "semelhanca_de_palavra(" in pagina.split("ORDER BY")[1]


def test_busca_aproximada_aceita_qualquer_palavra_parecida():
    db = SessaoQueGuarda()

    catalogo_repository.listar_produtos(db, 12, 0, palavras=["linho", "azul"], aproximada=True, ordem="relevancia")

    contagem = db.consultas[0].compile(dialect=postgresql.dialect())
    assert " OR " in str(contagem) and " ~ " not in str(contagem)
    assert catalogo_repository.SEMELHANCA_APROXIMADA in contagem.params.values()


def test_relevancia_sem_busca_cai_na_ordem_por_nome():
    db = SessaoQueGuarda()

    catalogo_repository.listar_produtos(db, 12, 0, ordem="relevancia")

    assert "ORDER BY produto.nome" in sql(db.consultas[1])
