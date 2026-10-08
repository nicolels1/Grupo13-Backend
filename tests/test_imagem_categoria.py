import pytest

from src.app import app
from src.models.catalogo import CategoriaProduto
from src.repositories import catalogo_repository
from src.use_cases import catalogo
from src.use_cases.erros import RecursoNaoEncontrado, RegraDeNegocio, ServicoIndisponivel
from src.utils.supabase_admin import ErroSupabase
from src.utils.upload import Arquivo, get_storage
from tests.apoio import PDF, PNG, SessaoFalsa, StorageFalso, api, funcionario  # noqa: F401 (api é fixture)

URL_PUBLICA = "https://teste.supabase.co/storage/v1/object/public/produtos/"


@pytest.fixture
def banco(monkeypatch):
    categorias = {
        1: CategoriaProduto(id_categoria=1, nome="Camisas", ativo=True, caminho_imagem=None),
        2: CategoriaProduto(id_categoria=2, nome="Vestidos", ativo=True, caminho_imagem="categorias/categoria-2/antiga.png"),
    }
    monkeypatch.setattr(catalogo_repository, "buscar_categoria", lambda db, i: categorias.get(i))
    monkeypatch.setattr(catalogo_repository, "listar_categorias", lambda db, ativo=None: list(categorias.values()))
    return categorias


def png():
    return Arquivo(nome="foto.png", tipo="image/png", conteudo=PNG)


# ---------- saída ----------

def test_categoria_sem_foto_sai_sem_url(banco):
    assert catalogo.montar_categoria(banco[1])["imagem_url"] is None


def test_categoria_com_foto_sai_com_a_url_publica(banco):
    assert catalogo.montar_categoria(banco[2])["imagem_url"] == URL_PUBLICA + "categorias/categoria-2/antiga.png"


# ---------- enviar e trocar ----------

def test_foto_sobe_na_pasta_categorias(banco):
    storage, db = StorageFalso(), SessaoFalsa()

    categoria = catalogo.trocar_imagem_categoria(db, storage, 1, png())

    assert categoria.caminho_imagem.startswith("categorias/categoria-1/")
    assert list(storage.enviados) == [("produtos", categoria.caminho_imagem)]
    assert storage.apagados == [] and db.commits == 1


def test_trocar_apaga_a_foto_antiga_depois_de_gravar(banco):
    storage = StorageFalso()

    categoria = catalogo.trocar_imagem_categoria(SessaoFalsa(), storage, 2, png())

    assert categoria.caminho_imagem != "categorias/categoria-2/antiga.png"
    assert storage.apagados == [("produtos", "categorias/categoria-2/antiga.png")]


def test_falha_no_banco_apaga_a_nova_e_mantem_a_antiga(banco):
    storage = StorageFalso()

    with pytest.raises(RuntimeError):
        catalogo.trocar_imagem_categoria(SessaoFalsa(erro_commit=RuntimeError("banco")), storage, 2, png())

    assert storage.apagados == list(storage.enviados)
    assert ("produtos", "categorias/categoria-2/antiga.png") not in storage.apagados


def test_categoria_inexistente_nao_sobe_arquivo(banco):
    storage = StorageFalso()
    with pytest.raises(RecursoNaoEncontrado):
        catalogo.trocar_imagem_categoria(SessaoFalsa(), storage, 99, png())
    assert storage.enviados == {}


def test_pdf_nao_vale_como_foto_de_categoria(banco):
    with pytest.raises(RegraDeNegocio, match="Tipo de arquivo"):
        catalogo.trocar_imagem_categoria(SessaoFalsa(), StorageFalso(), 1, Arquivo("x.pdf", "application/pdf", PDF))


def test_storage_fora_do_ar(banco):
    storage = StorageFalso(erro=ErroSupabase(503, "indisponivel", "timeout"))
    with pytest.raises(ServicoIndisponivel):
        catalogo.trocar_imagem_categoria(SessaoFalsa(), storage, 1, png())


# ---------- tirar ----------

def test_tirar_a_foto_zera_e_apaga_o_arquivo(banco):
    storage, db = StorageFalso(), SessaoFalsa()

    categoria = catalogo.remover_imagem_categoria(db, storage, 2)

    assert categoria.caminho_imagem is None and db.commits == 1
    assert storage.apagados == [("produtos", "categorias/categoria-2/antiga.png")]


def test_tirar_sem_foto_nao_faz_nada(banco):
    storage, db = StorageFalso(), SessaoFalsa()

    catalogo.remover_imagem_categoria(db, storage, 1)

    assert db.commits == 0 and storage.apagados == []


# ---------- rotas ----------

def test_rota_de_envio_multipart(api, banco):
    storage = StorageFalso()
    app.dependency_overrides[get_storage] = lambda: storage

    resposta = api(SessaoFalsa(), usuario=funcionario()).post(
        "/categorias/1/imagem", files={"arquivo": ("foto.png", PNG, "image/png")},
    )

    assert resposta.status_code == 200
    assert resposta.json()["imagem_url"].startswith(URL_PUBLICA + "categorias/categoria-1/")


def test_rota_de_envio_exige_permissao(api, banco):
    app.dependency_overrides[get_storage] = StorageFalso
    resposta = api(SessaoFalsa(), usuario=funcionario(), permitido=False).post(
        "/categorias/1/imagem", files={"arquivo": ("foto.png", PNG, "image/png")},
    )
    assert resposta.status_code == 403


def test_rota_de_remover(api, banco):
    app.dependency_overrides[get_storage] = StorageFalso

    resposta = api(SessaoFalsa(), usuario=funcionario()).delete("/categorias/2/imagem")

    assert resposta.status_code == 200 and resposta.json()["imagem_url"] is None


def test_listar_categorias_traz_a_foto(api, banco):
    resposta = api(SessaoFalsa()).get("/categorias")

    por_id = {c["id_categoria"]: c["imagem_url"] for c in resposta.json()["items"]}
    assert por_id == {1: None, 2: URL_PUBLICA + "categorias/categoria-2/antiga.png"}
