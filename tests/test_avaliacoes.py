from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from src.app import app
from src.models.avaliacoes import Avaliacao, DenunciaAvaliacao, FotoAvaliacao, VotoUtil
from src.models.vendas import ItemPedido, Pedido
from src.repositories import avaliacao_repository
from src.use_cases import avaliacoes
from src.use_cases.erros import Conflito, RecursoNaoEncontrado, RegraDeNegocio
from src.utils.upload import Arquivo, get_storage
from tests.apoio import PNG, SessaoFalsa, StorageFalso, api, funcionario  # noqa: F401
from tests.apoio_vendas import ID_CLIENTE, ID_OUTRO, cliente


def _agora():
    return datetime.now(timezone.utc)


class SessaoAvaliacoes(SessaoFalsa):
    """Guarda avaliações, fotos, votos e denúncias como o banco guardaria."""

    def __init__(self, banco, **kw):
        super().__init__(**kw)
        self.banco = banco

    def flush(self):
        super().flush()
        for objeto in self.adicionados:
            if isinstance(objeto, Avaliacao):
                objeto.criada_em = objeto.criada_em or _agora()
                self.banco.avaliacoes[objeto.id_avaliacao] = objeto
            elif isinstance(objeto, FotoAvaliacao) and objeto not in self.banco.fotos:
                self.banco.fotos.append(objeto)
            elif isinstance(objeto, VotoUtil):
                self.banco.votos.add((objeto.id_avaliacao, objeto.id_cliente))
            elif isinstance(objeto, DenunciaAvaliacao):
                self.banco.denuncias[objeto.id_denuncia] = objeto

    def commit(self):
        self.flush()
        super().commit()


@pytest.fixture
def banco(monkeypatch):
    estado = SimpleNamespace(
        itens={30: (ItemPedido(id_item=30, id_pedido=5, id_variante=10, quantidade=1, preco_unitario=10),
                    Pedido(id_pedido=5, id_cliente=ID_CLIENTE, status="entregue")),
               31: (ItemPedido(id_item=31, id_pedido=6, id_variante=10, quantidade=1, preco_unitario=10),
                    Pedido(id_pedido=6, id_cliente=ID_CLIENTE, status="pago"))},
        avaliacoes={}, fotos=[], votos=set(), denuncias={},
    )

    def autor(db, id_avaliacao):
        avaliacao = estado.avaliacoes.get(id_avaliacao)
        return None if avaliacao is None else estado.itens[avaliacao.id_item_pedido][1].id_cliente

    def detalhar(db, id_avaliacao):
        a = estado.avaliacoes[id_avaliacao]
        return {**{c.key: getattr(a, c.key) for c in Avaliacao.__table__.columns}, "id_produto": 1, "produto": "Camisa",
                "cor": "Azul", "tamanho": "M", "autor": "Marina Costa",
                "votos_util": sum(1 for v, _ in estado.votos if v == id_avaliacao), "denuncias_pendentes": 0}

    r = avaliacao_repository
    m = monkeypatch.setattr
    m(r, "item_com_pedido", lambda db, i: estado.itens.get(i))
    m(r, "avaliacao_do_item", lambda db, i: next((a for a in estado.avaliacoes.values() if a.id_item_pedido == i), None))
    m(r, "buscar_avaliacao", lambda db, i, travar=False: estado.avaliacoes.get(i))
    m(r, "autor_da_avaliacao", autor)
    m(r, "detalhar_avaliacao", detalhar)
    m(r, "fotos_das_avaliacoes", lambda db, ids: {i: [f for f in estado.fotos if f.id_avaliacao == i] for i in ids})
    m(r, "buscar_voto", lambda db, a, c: (a, c) in estado.votos or None)
    m(r, "denuncia_do_cliente", lambda db, a, c: next(
        (d for d in estado.denuncias.values() if d.id_avaliacao == a and d.id_cliente == c), None))
    m(r, "buscar_denuncia", lambda db, i, travar=False: estado.denuncias.get(i))
    m(r, "detalhar_denuncia", lambda db, i: estado.denuncias[i])
    return estado


def avaliar(banco, item=30, nota=4):
    return avaliacoes.criar(SessaoAvaliacoes(banco), StorageFalso(), cliente(), {"id_item_pedido": item, "nota": nota,
                                                                                 "texto": "Gostei"})


def foto():
    return Arquivo("f.png", "image/png", PNG)


# ---------- criar e editar ----------

def test_avalia_item_entregue(banco):
    avaliacao = avaliar(banco)
    assert avaliacao["status"] == "publicada" and avaliacao["nota"] == 4 and avaliacao["autor"] == "Marina"


def test_nao_avalia_antes_de_receber(banco):
    with pytest.raises(RegraDeNegocio, match="depois de receber"):
        avaliar(banco, item=31)


def test_item_de_outra_pessoa(banco):
    with pytest.raises(RecursoNaoEncontrado):
        avaliacoes.criar(SessaoAvaliacoes(banco), StorageFalso(), cliente(ID_OUTRO), {"id_item_pedido": 30, "nota": 5})


def test_uma_avaliacao_por_item(banco):
    avaliar(banco)
    with pytest.raises(Conflito, match="já foi avaliado"):
        avaliar(banco)


def test_edita_dentro_de_7_dias(banco):
    id_avaliacao = avaliar(banco)["id_avaliacao"]
    editada = avaliacoes.alterar(SessaoAvaliacoes(banco), StorageFalso(), cliente(), id_avaliacao, {"nota": 2})
    assert editada["nota"] == 2 and editada["editada_em"] is not None


def test_nao_edita_depois_de_7_dias(banco):
    id_avaliacao = avaliar(banco)["id_avaliacao"]
    banco.avaliacoes[id_avaliacao].criada_em = _agora() - timedelta(days=8)
    with pytest.raises(RegraDeNegocio, match="7 dias"):
        avaliacoes.alterar(SessaoAvaliacoes(banco), StorageFalso(), cliente(), id_avaliacao, {"nota": 2})


def test_outra_pessoa_nao_edita(banco):
    id_avaliacao = avaliar(banco)["id_avaliacao"]
    with pytest.raises(RecursoNaoEncontrado):
        avaliacoes.alterar(SessaoAvaliacoes(banco), StorageFalso(), cliente(ID_OUTRO), id_avaliacao, {"nota": 1})


# ---------- fotos ----------

def test_ate_5_fotos_na_area_privada(banco):
    id_avaliacao = avaliar(banco)["id_avaliacao"]
    storage = StorageFalso()
    for _ in range(5):
        resultado = avaliacoes.adicionar_foto(SessaoAvaliacoes(banco), storage, cliente(), id_avaliacao, foto())

    assert [f["ordem"] for f in resultado["fotos"]] == [1, 2, 3, 4, 5]
    assert all(bucket == "avaliacoes" for bucket, _ in storage.enviados)
    assert "token" in resultado["fotos"][0]["url"]
    with pytest.raises(RegraDeNegocio, match="5 fotos"):
        avaliacoes.adicionar_foto(SessaoAvaliacoes(banco), storage, cliente(), id_avaliacao, foto())


# ---------- votos e denúncias ----------

def test_voto_util_uma_vez_e_nao_na_propria(banco):
    id_avaliacao = avaliar(banco)["id_avaliacao"]
    with pytest.raises(RegraDeNegocio, match="própria"):
        avaliacoes.votar_util(SessaoAvaliacoes(banco), StorageFalso(), cliente(), id_avaliacao)

    votada = avaliacoes.votar_util(SessaoAvaliacoes(banco), StorageFalso(), cliente(ID_OUTRO), id_avaliacao)
    assert votada["votos_util"] == 1
    with pytest.raises(Conflito):
        avaliacoes.votar_util(SessaoAvaliacoes(banco), StorageFalso(), cliente(ID_OUTRO), id_avaliacao)


def test_denuncia_uma_vez(banco):
    id_avaliacao = avaliar(banco)["id_avaliacao"]
    denuncia = avaliacoes.denunciar(SessaoAvaliacoes(banco), cliente(ID_OUTRO), id_avaliacao, "ofensiva")
    assert denuncia.status == "pendente"
    with pytest.raises(Conflito):
        avaliacoes.denunciar(SessaoAvaliacoes(banco), cliente(ID_OUTRO), id_avaliacao, "de novo")


# ---------- moderação ----------

def test_denuncia_procedente_oculta_e_tira_as_fotos(banco):
    id_avaliacao = avaliar(banco)["id_avaliacao"]
    avaliacoes.adicionar_foto(SessaoAvaliacoes(banco), StorageFalso(), cliente(), id_avaliacao, foto())
    denuncia = avaliacoes.denunciar(SessaoAvaliacoes(banco), cliente(ID_OUTRO), id_avaliacao, "ofensiva")

    avaliacoes.analisar_denuncia(SessaoAvaliacoes(banco), funcionario(), denuncia.id_denuncia, True, None)

    avaliacao = banco.avaliacoes[id_avaliacao]
    assert avaliacao.status == "oculta" and avaliacao.motivo_ocultacao == "ofensiva"
    assert denuncia.status == "procedente" and denuncia.analisada_em is not None
    # quem avaliou ainda vê a própria avaliação, mas sem as fotos
    vista = avaliacoes.detalhar(SessaoAvaliacoes(banco), StorageFalso(), id_avaliacao, cliente())
    assert vista["fotos"] == [] and vista["motivo_ocultacao"] is None
    # outras pessoas não veem
    with pytest.raises(RecursoNaoEncontrado):
        avaliacoes.detalhar(SessaoAvaliacoes(banco), StorageFalso(), id_avaliacao, None)
    with pytest.raises(Conflito, match="já foi analisada"):
        avaliacoes.analisar_denuncia(SessaoAvaliacoes(banco), funcionario(), denuncia.id_denuncia, False, None)


def test_denuncia_improcedente_mantem_publicada(banco):
    id_avaliacao = avaliar(banco)["id_avaliacao"]
    denuncia = avaliacoes.denunciar(SessaoAvaliacoes(banco), cliente(ID_OUTRO), id_avaliacao, "não gostei")
    avaliacoes.analisar_denuncia(SessaoAvaliacoes(banco), funcionario(), denuncia.id_denuncia, False, None)
    assert banco.avaliacoes[id_avaliacao].status == "publicada" and denuncia.status == "improcedente"


def test_ocultar_direto_com_motivo(banco):
    id_avaliacao = avaliar(banco)["id_avaliacao"]
    oculta = avaliacoes.ocultar(SessaoAvaliacoes(banco), StorageFalso(), funcionario(), id_avaliacao, "dados pessoais")
    assert oculta["status"] == "oculta" and oculta["motivo_ocultacao"] == "dados pessoais"
    with pytest.raises(RegraDeNegocio, match="já está oculta"):
        avaliacoes.ocultar(SessaoAvaliacoes(banco), StorageFalso(), funcionario(), id_avaliacao, "de novo")


def test_moderacao_exige_permissao(api, banco):
    app.dependency_overrides[get_storage] = StorageFalso
    assert api(SessaoFalsa(), usuario=funcionario(), permitido=False).get("/moderacao/denuncias").status_code == 403


def test_rota_de_foto(api, banco):
    id_avaliacao = avaliar(banco)["id_avaliacao"]
    app.dependency_overrides[get_storage] = StorageFalso
    resposta = api(SessaoAvaliacoes(banco), usuario=cliente()).post(
        f"/avaliacoes/{id_avaliacao}/fotos", files={"arquivo": ("f.png", PNG, "image/png")})
    assert resposta.status_code == 201 and len(resposta.json()["fotos"]) == 1


# ---------- avaliações do produto (vitrine) ----------

@pytest.fixture
def do_produto(monkeypatch):
    estado = SimpleNamespace(filtros=None)

    def listar(db, limit, offset, **filtros):
        estado.filtros = filtros
        return [], 0

    m = monkeypatch.setattr
    m(avaliacao_repository, "listar_avaliacoes", listar)
    m(avaliacao_repository, "media_do_produto", lambda db, i: Decimal("4.5"))
    m(avaliacao_repository, "contagem_por_nota", lambda db, i: {5: 3, 4: 1})
    m(avaliacao_repository, "fotos_das_avaliacoes", lambda db, ids: {})
    return estado


def test_avaliacoes_do_produto_contam_de_5_a_1_com_zero_nas_que_faltam(do_produto):
    resultado = avaliacoes.do_produto(SessaoFalsa(), StorageFalso(), 1, 10, 0)

    assert resultado["media"] == Decimal("4.5")
    assert [(c["nota"], c["quantidade"]) for c in resultado["contagem_por_nota"]] == [
        (5, 3), (4, 1), (3, 0), (2, 0), (1, 0)]
    assert do_produto.filtros == {"id_produto": 1, "status": "publicada", "com_fotos": None}


def test_rota_filtra_avaliacoes_com_fotos(api, do_produto):
    app.dependency_overrides[get_storage] = StorageFalso

    resposta = api(SessaoFalsa()).get("/produtos/1/avaliacoes", params={"com_fotos": "true"})

    assert resposta.status_code == 200
    assert do_produto.filtros["com_fotos"] is True
    assert len(resposta.json()["contagem_por_nota"]) == 5


# ---------- consultas: o SQL monta para o PostgreSQL ----------

def sql(consulta):
    return str(consulta.compile(dialect=postgresql.dialect()))


@pytest.mark.parametrize("com_fotos, comparacao", [(True, "> %"), (False, "= %")])
def test_filtro_de_fotos_conta_as_fotos_da_avaliacao(com_fotos, comparacao):
    consulta = sql(avaliacao_repository.consulta_avaliacoes(id_produto=1, com_fotos=com_fotos))

    trecho_fotos = consulta.split("FROM foto_avaliacao")[1]
    assert "foto_avaliacao.id_avaliacao = avaliacao.id_avaliacao" in trecho_fotos
    assert comparacao in trecho_fotos


def test_contagem_por_nota_agrupa_as_publicadas_do_produto():
    class SessaoQueGuarda:
        consulta = None

        def execute(self, consulta):
            self.consulta = consulta
            return [(5, 2), (3, 1)]

    db = SessaoQueGuarda()

    assert avaliacao_repository.contagem_por_nota(db, 1) == {5: 2, 3: 1}
    texto = sql(db.consulta)
    assert "GROUP BY avaliacao.nota" in texto and "avaliacao.status = " in texto
