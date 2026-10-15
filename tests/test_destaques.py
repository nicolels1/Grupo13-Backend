import pytest
from pydantic import ValidationError

from src.app import app
from src.dependencies.permissoes import get_usuario_opcional
from src.dependencies.supabase import get_storage
from src.entities.destaques import DestaqueCriar
from src.models.catalogo import Destaque
from src.repositories import destaque_repository
from src.use_cases import destaques
from src.use_cases.erros import RecursoNaoEncontrado
from src.utils.upload import Arquivo
from tests.apoio import PNG, SessaoFalsa, StorageFalso, api, funcionario  # noqa: F401 (api é fixture)

URL_PUBLICA = "https://teste.supabase.co/storage/v1/object/public/produtos/"
SLIDE = dict(titulo="Linho", texto="Peças leves para o verão.", rotulo_botao="Ver as novidades", link="/loja/produtos")


@pytest.fixture
def banco(monkeypatch):
    slides = {
        1: Destaque(id_destaque=1, ordem=1, ativo=True, caminho_imagem=None, **SLIDE),
        2: Destaque(id_destaque=2, ordem=2, ativo=False, caminho_imagem="destaques/destaque-2/antiga.png",
                    **{**SLIDE, "titulo": "Alfaiataria"}),
    }

    def listar(db, so_ativos):
        return [s for s in sorted(slides.values(), key=lambda s: s.ordem) if s.ativo or not so_ativos]

    monkeypatch.setattr(destaque_repository, "listar_destaques", listar)
    monkeypatch.setattr(destaque_repository, "buscar_destaque", lambda db, i: slides.get(i))
    monkeypatch.setattr(destaque_repository, "proxima_ordem", lambda db: max(s.ordem for s in slides.values()) + 1)
    return slides


def png():
    return Arquivo(nome="foto.png", tipo="image/png", conteudo=PNG)


# ---------- dados que chegam ----------

def test_link_precisa_ser_uma_pagina_da_loja():
    assert DestaqueCriar(**{**SLIDE, "link": " /loja/ajuda#trocas "}).link == "/loja/ajuda#trocas"
    for link in ["https://golpe.example", "//golpe.example", "javascript:alert(1)", "loja/produtos"]:
        with pytest.raises(ValidationError, match="página da loja"):
            DestaqueCriar(**{**SLIDE, "link": link})


# ---------- regras ----------

def test_slide_novo_sem_ordem_entra_no_fim(banco):
    db = SessaoFalsa(primeiro_id=3)

    destaque = destaques.criar_destaque(db, {**SLIDE, "ordem": None, "ativo": True})

    assert (destaque.id_destaque, destaque.ordem, db.commits) == (3, 3, 1)


def test_saida_leva_a_url_publica_da_foto(banco):
    assert destaques.montar_destaque(banco[1])["imagem_url"] is None
    assert destaques.montar_destaque(banco[2])["imagem_url"] == URL_PUBLICA + "destaques/destaque-2/antiga.png"


def test_trocar_foto_apaga_a_antiga_depois_de_gravar(banco):
    storage = StorageFalso()

    destaque = destaques.trocar_imagem_destaque(SessaoFalsa(), storage, 2, png())

    assert destaque.caminho_imagem.startswith("destaques/destaque-2/")
    assert storage.apagados == [("produtos", "destaques/destaque-2/antiga.png")]


def test_falha_no_banco_desfaz_a_foto_nova_e_mantem_a_antiga(banco):
    storage = StorageFalso()

    with pytest.raises(RuntimeError):
        destaques.trocar_imagem_destaque(SessaoFalsa(erro_commit=RuntimeError("banco")), storage, 2, png())

    assert storage.apagados == list(storage.enviados)
    assert ("produtos", "destaques/destaque-2/antiga.png") not in storage.apagados


def test_apagar_slide_apaga_a_foto(banco):
    storage, db = StorageFalso(), SessaoFalsa()

    destaques.remover_destaque(db, storage, 2)

    assert db.apagados == [banco[2]]
    assert storage.apagados == [("produtos", "destaques/destaque-2/antiga.png")]


def test_slide_inexistente(banco):
    with pytest.raises(RecursoNaoEncontrado):
        destaques.alterar_destaque(SessaoFalsa(), 99, {"ativo": False})


# ---------- rotas ----------

def test_loja_ve_so_os_slides_ativos(api, banco):
    resposta = api(SessaoFalsa()).get("/destaques")

    assert resposta.status_code == 200
    assert [d["titulo"] for d in resposta.json()["items"]] == ["Linho"]


def test_quem_gerencia_o_catalogo_ve_tambem_os_desativados(api, banco):
    cliente = api(SessaoFalsa(), usuario=funcionario())
    app.dependency_overrides[get_usuario_opcional] = lambda: funcionario()

    resposta = cliente.get("/destaques")

    assert [d["titulo"] for d in resposta.json()["items"]] == ["Linho", "Alfaiataria"]


def test_criar_slide_exige_permissao(api, banco):
    assert api(SessaoFalsa()).post("/destaques", json=SLIDE).status_code == 401
    assert api(SessaoFalsa(), usuario=funcionario(), permitido=False).post("/destaques", json=SLIDE).status_code == 403


def test_criar_slide_com_permissao(api, banco):
    resposta = api(SessaoFalsa(primeiro_id=3), usuario=funcionario()).post("/destaques", json=SLIDE)

    assert resposta.status_code == 201
    assert resposta.json() == {**SLIDE, "id_destaque": 3, "imagem_url": None, "ordem": 3, "ativo": True}


def test_link_externo_retorna_422(api, banco):
    resposta = api(SessaoFalsa(), usuario=funcionario()).post("/destaques", json={**SLIDE, "link": "https://golpe.example"})

    assert resposta.status_code == 422


def test_desativar_slide(api, banco):
    resposta = api(SessaoFalsa(), usuario=funcionario()).patch("/destaques/1", json={"ativo": False})

    assert (resposta.status_code, resposta.json()["ativo"]) == (200, False)


def test_apagar_slide_retorna_204(api, banco):
    app.dependency_overrides[get_storage] = StorageFalso

    resposta = api(SessaoFalsa(), usuario=funcionario()).delete("/destaques/2")

    assert resposta.status_code == 204


def test_enviar_foto_do_slide(api, banco):
    app.dependency_overrides[get_storage] = StorageFalso

    resposta = api(SessaoFalsa(), usuario=funcionario()).post(
        "/destaques/1/imagem", files={"arquivo": ("foto.png", PNG, "image/png")}
    )

    assert resposta.status_code == 200
    assert resposta.json()["imagem_url"].startswith(URL_PUBLICA + "destaques/destaque-1/")
