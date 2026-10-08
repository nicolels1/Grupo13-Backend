from decimal import Decimal

import pytest

from src.models.catalogo import ImagemProduto, Produto, Variante
from src.repositories import catalogo_repository
from src.use_cases import arquivos, catalogo
from src.use_cases.erros import RecursoNaoEncontrado, RegraDeNegocio, ServicoIndisponivel
from src.utils.supabase_admin import ErroSupabase
from src.utils.upload import Arquivo, get_storage
from src.app import app
from tests.apoio import PDF, PNG, SessaoFalsa, StorageFalso, api, funcionario  # noqa: F401


@pytest.fixture
def banco(monkeypatch):
    produto = Produto(id_produto=5, id_categoria=1, nome="Camisa", descricao_tecnica="t", descricao_cliente="c", ativo=True)
    variante = Variante(id_variante=50, id_produto=5, sku="CAM", cor="Azul", tamanho="M", preco=Decimal("10"), ativo=True)
    imagem = ImagemProduto(id_imagem=9, id_produto=5, cor=None, caminho_arquivo="produto-5/a.png", ordem=1)
    r = catalogo_repository
    monkeypatch.setattr(r, "buscar_produto", lambda db, i: produto if i == 5 else None)
    monkeypatch.setattr(r, "variantes_dos_produtos", lambda db, ids: {i: [variante] for i in ids})
    monkeypatch.setattr(r, "proxima_ordem_de_imagem", lambda db, i: 2)
    monkeypatch.setattr(r, "buscar_imagem", lambda db, i: imagem if i == 9 else None)
    return imagem


def png():
    return Arquivo(nome="foto.png", tipo="image/png", conteudo=PNG)


# ---------- regras de arquivo ----------

def test_tipo_declarado_precisa_bater_com_o_conteudo():
    with pytest.raises(RegraDeNegocio, match="Tipo de arquivo"):
        arquivos.conferir(arquivos.FOTO_PRODUTO, Arquivo("x.png", "image/png", PDF))


def test_pdf_nao_vale_como_foto_de_produto():
    with pytest.raises(RegraDeNegocio, match="Tipo de arquivo"):
        arquivos.conferir(arquivos.FOTO_PRODUTO, Arquivo("x.pdf", "application/pdf", PDF))


def test_pdf_vale_como_anexo():
    arquivos.conferir(arquivos.ANEXO_CHAMADO, Arquivo("x.pdf", "application/pdf", PDF))


def test_arquivo_grande_demais():
    grande = Arquivo("x.png", "image/png", PNG + b"0" * arquivos.FOTO_PRODUTO.limite)
    with pytest.raises(RegraDeNegocio, match="grande demais"):
        arquivos.conferir(arquivos.FOTO_PRODUTO, grande)


def test_caminho_aleatorio_na_pasta_do_registro():
    caminho = arquivos.novo_caminho("produto-5", "image/png")
    assert caminho.startswith("produto-5/") and caminho.endswith(".png")
    assert caminho != arquivos.novo_caminho("produto-5", "image/png")


# ---------- fotos de produto ----------

def test_foto_sobe_e_entra_no_fim(banco):
    storage, db = StorageFalso(), SessaoFalsa()

    imagem = catalogo.adicionar_imagem(db, storage, 5, png(), None, None)

    assert imagem["ordem"] == 2 and imagem["cor"] is None
    assert imagem["url"].startswith("https://teste.supabase.co/storage/v1/object/public/produtos/produto-5/")
    assert list(storage.enviados) == [("produtos", db.adicionados[0].caminho_arquivo)]


def test_cor_precisa_ser_de_uma_variante(banco):
    with pytest.raises(RegraDeNegocio, match="cor Verde"):
        catalogo.adicionar_imagem(SessaoFalsa(), StorageFalso(), 5, png(), "Verde", None)


def test_cor_usa_a_grafia_da_variante(banco):
    imagem = catalogo.adicionar_imagem(SessaoFalsa(), StorageFalso(), 5, png(), "azul", None)
    assert imagem["cor"] == "Azul"


def test_produto_inexistente_nao_sobe_arquivo(banco):
    storage = StorageFalso()
    with pytest.raises(RecursoNaoEncontrado):
        catalogo.adicionar_imagem(SessaoFalsa(), storage, 99, png(), None, None)
    assert storage.enviados == {}


def test_falha_no_banco_apaga_o_arquivo(banco):
    storage = StorageFalso()
    with pytest.raises(RuntimeError):
        catalogo.adicionar_imagem(SessaoFalsa(erro_commit=RuntimeError("banco")), storage, 5, png(), None, None)
    assert storage.apagados == list(storage.enviados)


def test_storage_fora_do_ar(banco):
    storage = StorageFalso(erro=ErroSupabase(503, "indisponivel", "timeout"))
    with pytest.raises(ServicoIndisponivel):
        catalogo.adicionar_imagem(SessaoFalsa(), storage, 5, png(), None, None)


def test_alterar_ordem_e_cor(banco):
    imagem = catalogo.alterar_imagem(SessaoFalsa(), 9, {"ordem": 3, "cor": "AZUL"})
    assert (imagem["ordem"], imagem["cor"]) == (3, "Azul")


def test_rota_de_envio_multipart(api, banco):
    storage = StorageFalso()
    app.dependency_overrides[get_storage] = lambda: storage

    resposta = api(SessaoFalsa(), usuario=funcionario()).post(
        "/produtos/5/imagens", files={"arquivo": ("foto.png", PNG, "image/png")}, data={"cor": "Azul"},
    )

    assert resposta.status_code == 201 and resposta.json()["cor"] == "Azul"
    assert len(storage.enviados) == 1


def test_rota_de_envio_exige_permissao(api, banco):
    app.dependency_overrides[get_storage] = StorageFalso
    resposta = api(SessaoFalsa(), usuario=funcionario(), permitido=False).post(
        "/produtos/5/imagens", files={"arquivo": ("foto.png", PNG, "image/png")},
    )
    assert resposta.status_code == 403


# ---------- apagar ----------

def test_apagar_tira_a_linha_e_depois_o_arquivo(banco):
    storage, db = StorageFalso(), SessaoFalsa()

    catalogo.remover_imagem(db, storage, 9)

    assert db.apagados == [banco] and db.commits == 1
    assert storage.apagados == [("produtos", "produto-5/a.png")]


def test_falha_no_banco_nao_apaga_o_arquivo(banco):
    storage = StorageFalso()

    with pytest.raises(RuntimeError):
        catalogo.remover_imagem(SessaoFalsa(erro_commit=RuntimeError("banco")), storage, 9)

    assert storage.apagados == []


def test_apagar_foto_inexistente(banco):
    with pytest.raises(RecursoNaoEncontrado):
        catalogo.remover_imagem(SessaoFalsa(), StorageFalso(), 99)


def test_rota_de_apagar(api, banco):
    app.dependency_overrides[get_storage] = StorageFalso

    resposta = api(SessaoFalsa(), usuario=funcionario()).delete("/imagens/9")

    assert resposta.status_code == 204


def test_rota_de_apagar_exige_permissao(api, banco):
    app.dependency_overrides[get_storage] = StorageFalso
    resposta = api(SessaoFalsa(), usuario=funcionario(), permitido=False).delete("/imagens/9")
    assert resposta.status_code == 403
