import re
from datetime import date, timedelta
from typing import get_args

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from src.app import app
from src.database.session import get_db
from src.dependencies import permissoes as dependencias
from src.dependencies.permissoes import get_usuario_ativo
from src.entities.modelos_acesso import CodigoPermissao
from src.repositories import estoque_repository, marca_repository, pedido_repository, permissao_repository
# as consultas de verdade, guardadas antes do registro_sem_marca do conftest trocá-las
from src.repositories.marca_repository import da_imagem, da_variante, do_pedido
from src.routes import atendimento, avaliacoes, balcao, catalogo, transferencias, vendas
from src.use_cases import fidelidade
from src.use_cases.erros import SemPermissao
from src.use_cases.permissoes import conferir_marca
from tests.apoio import SessaoFalsa, funcionario

# todas as permissões de área; a marca vem separada em cada teste
DE_AREA = {c for c in get_args(CodigoPermissao) if not c.startswith("ver_marca_")}


@pytest.fixture
def entrar(monkeypatch):
    """Cliente da API logado com todas as permissões de área e só as marcas pedidas."""

    def montar(*marcas):
        app.dependency_overrides[get_db] = lambda: SessaoFalsa()
        app.dependency_overrides[get_usuario_ativo] = lambda: funcionario()
        monkeypatch.setattr(permissao_repository, "modelo_eh_admin", lambda db, i: False)
        monkeypatch.setattr(permissao_repository, "codigos_do_modelo",
                            lambda db, i: DE_AREA | {f"ver_marca_{m}" for m in marcas})
        monkeypatch.setattr(permissao_repository, "excecoes_do_usuario", lambda db, i: {})
        return TestClient(app)

    yield montar
    app.dependency_overrides.clear()


def tudo_da(monkeypatch, marca):
    """Todo registro consultado é dessa marca."""
    for nome in ["da_unidade", "do_pedido", "da_transferencia", "do_chamado", "do_produto", "da_variante",
                 "da_imagem"]:
        monkeypatch.setattr(marca_repository, nome, lambda db, i: {marca})


# ---------- regra ----------

def test_registro_de_uma_marca_que_a_pessoa_nao_ve():
    conferir_marca({"lorenzi", "vulto"}, {"vulto"})
    conferir_marca({"lorenzi"}, set())  # dúvida geral, sem marca
    conferir_marca({"lorenzi"}, {"lorenzi", "vulto"})  # chamado ligado às duas: basta ver uma
    with pytest.raises(SemPermissao, match="marca vulto"):
        conferir_marca({"lorenzi"}, {"vulto"})


# ---------- detalhe e escrita pelo id do caminho ----------

# cada caminho da equipe com o id de um registro que tem marca, e a dependência que confere a marca dele
DO_CAMINHO = [
    (r"^/atendimento/chamados/\{id_chamado\}", dependencias.marca_do_chamado),
    (r"^/vendas/pedidos/\{id_pedido\}", dependencias.marca_do_pedido),
    (r"^/balcao/pedidos/\{id_pedido\}", dependencias.marca_do_pedido),
    (r"^/transferencias/\{id_transferencia\}", dependencias.marca_da_transferencia),
    (r"^/produtos/\{id_produto\}", dependencias.marca_do_produto),
    (r"^/variantes/\{id_variante\}", dependencias.marca_da_variante),
    (r"^/imagens/\{id_imagem\}", dependencias.marca_da_imagem),
]


def test_toda_rota_da_equipe_com_registro_de_marca_confere_a_marca():
    conferidas = 0
    rotas = [r for m in (atendimento, avaliacoes, balcao, catalogo, transferencias, vendas) for r in m.router.routes]
    for rota in rotas:
        if not isinstance(rota, APIRoute):
            continue
        # a página do produto é pública: a vitrine mostra as duas marcas
        if rota.path == "/produtos/{id_produto}" and rota.methods == {"GET"}:
            continue
        for padrao, dependencia in DO_CAMINHO:
            if re.match(padrao, rota.path) and not rota.path.startswith("/produtos/{id_produto}/avaliacoes"):
                assert dependencia in [d.dependency for d in rota.dependencies], (rota.methods, rota.path)
                conferidas += 1
    # 12 de chamado, 7 do catálogo, 5 de pedido, 4 de transferência e 2 do balcão
    assert conferidas == 30


def test_detalhe_de_outra_marca_responde_403(entrar, monkeypatch):
    tudo_da(monkeypatch, "vulto")

    resposta = entrar("lorenzi").get("/vendas/pedidos/1")

    assert resposta.status_code == 403
    assert "marca vulto" in resposta.json()["detail"]


def test_detalhe_da_marca_que_a_pessoa_ve_passa(entrar, monkeypatch):
    tudo_da(monkeypatch, "vulto")
    monkeypatch.setattr(pedido_repository, "pedido_com_nomes", lambda db, i: None)

    assert entrar("vulto").get("/vendas/pedidos/1").status_code == 404


# ---------- escrita com a unidade ou a marca no corpo ----------

def _regra(marca):
    return {"marca": marca, "valida_desde": str(date.today() + timedelta(days=2)), "valor_ponto": "0.05",
            "pontos_por_real": "1", "multiplicador_online": "2", "bonus_ouro": "0.5", "limite_prata": 1000,
            "limite_ouro": 5000, "resgate_minimo": 100}


ESCRITAS = [
    ("post", "/movimentacoes-estoque",
     {"id_variante": 1, "id_unidade": 2, "canal": "loja_fisica", "tipo": "recebimento", "quantidade": 1}),
    ("post", "/realocacoes", {"id_variante": 1, "id_unidade": 2, "canal_origem": "loja_fisica", "quantidade": 1}),
    ("put", "/estoque/minimo", {"id_variante": 1, "id_unidade": 2, "canal": "loja_fisica", "estoque_minimo": 1}),
    ("post", "/transferencias", {"id_unidade_origem": 1, "id_unidade_destino": 2, "itens": [
        {"id_variante": 1, "canal_saida": "loja_fisica", "canal_entrada": "loja_fisica", "quantidade": 1}]}),
    ("post", "/vendas/pedidos", {"id_unidade": 2, "itens": [{"id_variante": 1, "quantidade": 1}],
                                 "pagamentos": [{"metodo": "pix", "valor": "10.00"}]}),
    ("post", "/produtos", {"id_categoria": 1, "nome": "Camisa", "descricao_tecnica": "t",
                           "descricao_cliente": "c", "marca": "vulto"}),
    ("post", "/fidelidade/regras", _regra("vulto")),
]


@pytest.mark.parametrize("metodo, rota, corpo", ESCRITAS, ids=[e[1] for e in ESCRITAS])
def test_escrita_em_outra_marca_responde_403(entrar, monkeypatch, metodo, rota, corpo):
    tudo_da(monkeypatch, "vulto")

    resposta = getattr(entrar("lorenzi"), metodo)(rota, json=corpo)

    assert resposta.status_code == 403
    assert "marca vulto" in resposta.json()["detail"]


def test_passar_o_produto_para_a_marca_que_nao_ve(entrar, monkeypatch):
    tudo_da(monkeypatch, "lorenzi")

    resposta = entrar("lorenzi").patch("/produtos/1", json={"marca": "vulto"})

    assert resposta.status_code == 403


def test_resgate_no_caixa_da_outra_marca(entrar):
    resposta = entrar("lorenzi").get("/fidelidade/resgate/caixa",
                                     params={"cpf": "52998224725", "marca": "vulto", "pontos": 10})
    assert resposta.status_code == 403


# ---------- listas que ficam na marca da pessoa ----------

def test_passivo_e_pontos_do_cliente_so_da_marca_que_ve(entrar, monkeypatch):
    duas = [{"marca": "lorenzi", "pontos": 1, "reais": 1, "por_valor": []},
            {"marca": "vulto", "pontos": 2, "reais": 2, "por_valor": []}]
    monkeypatch.setattr(fidelidade, "passivo", lambda db, em: duas)
    saldos = [{"marca": m, "saldo": 1, "valor_em_reais": "1", "categoria": "bronze",
               "categoria_desde": "2026-10-01T00:00:00Z", "ganhos_no_ano": 0, "proximo_vencimento": None, "pontos_no_proximo_vencimento": 0}
              for m in ("lorenzi", "vulto")]
    monkeypatch.setattr(fidelidade, "pontos_do_cliente", lambda db, *a: {"saldos": saldos})
    cliente = entrar("vulto")

    assert [p["marca"] for p in cliente.get("/fidelidade/passivo").json()["items"]] == ["vulto"]
    resposta = cliente.get("/fidelidade/clientes/pontos", params={"id_cliente_sem_conta": 1})
    assert [s["marca"] for s in resposta.json()["saldos"]] == ["vulto"]


def test_consultar_peca_no_balcao_fica_na_marca(entrar, monkeypatch):
    filtros = []
    monkeypatch.setattr(estoque_repository, "listar_estoque",
                        lambda db, limit, offset, **f: filtros.append(f) or ([], 0))

    assert entrar("lorenzi").get("/balcao/estoque").status_code == 200
    assert filtros[0]["marca"] == "lorenzi"


# quem modera cuida da rede inteira: oculta a avaliação de qualquer marca
def test_moderacao_nao_depende_da_marca():
    rotas = [r for r in avaliacoes.router.routes if r.path.startswith("/moderacao")]
    assert rotas and all(not r.dependencies for r in rotas)


def test_pedido_da_outra_marca_nao_aparece_na_busca_do_balcao(entrar, monkeypatch):
    tudo_da(monkeypatch, "vulto")

    assert entrar("lorenzi").get("/balcao/pedidos", params={"id_pedido": 1}).status_code == 403


# ---------- consultas: o SQL monta para o PostgreSQL ----------

def sql(consulta):
    return str(consulta.compile(dialect=postgresql.dialect()))


def test_marca_de_cada_registro_sai_da_unidade_ou_do_produto():
    capturadas = []

    class Sessao:
        def scalars(self, consulta):
            capturadas.append(sql(consulta))
            return []

    db = Sessao()
    do_pedido(db, 1)
    da_variante(db, 1)
    da_imagem(db, 1)

    assert "pedido.id_pedido = %(id_pedido_1)s" in capturadas[0] and "unidade.marca" in capturadas[0]
    assert "variante.id_variante = %(id_variante_1)s" in capturadas[1] and "produto.marca" in capturadas[1]
    assert "imagem_produto.id_imagem = %(id_imagem_1)s" in capturadas[2] and "produto.marca" in capturadas[2]
