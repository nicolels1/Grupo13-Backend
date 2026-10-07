import uuid
from datetime import datetime, timezone

import pytest

from src.app import app
from src.models.atendimento import Chamado, Mensagem
from src.repositories import atendimento_repository
from src.use_cases import atendimento
from src.use_cases.erros import RecursoNaoEncontrado, RegraDeNegocio
from src.utils.upload import Arquivo, get_storage
from tests.apoio import PDF, PNG, SessaoFalsa, StorageFalso, api, funcionario  # noqa: F401

ID_CLIENTE = uuid.UUID("44444444-4444-4444-4444-444444444444")
ID_OUTRO = uuid.UUID("55555555-5555-5555-5555-555555555555")


def cliente(id_usuario=ID_CLIENTE):
    return funcionario(id_usuario=id_usuario, tipo_conta="cliente", id_modelo_acesso=None, nome="Marina")


@pytest.fixture
def banco(monkeypatch):
    chamado = Chamado(id_chamado=3, id_cliente=ID_CLIENTE, status="aberto", categoria="duvida", assunto="a", descricao="d")
    mensagens = {
        20: Mensagem(id_mensagem=20, id_chamado=3, id_autor=ID_CLIENTE, anexo_caminho="chamado-3/a.pdf",
                     anexo_nome="nota.pdf", interna=False),
        21: Mensagem(id_mensagem=21, id_chamado=3, id_autor=ID_CLIENTE, anexo_caminho="chamado-3/b.pdf",
                     anexo_nome="interno.pdf", interna=True),
    }
    monkeypatch.setattr(atendimento_repository, "buscar_chamado", lambda db, i: chamado if i == 3 else None)
    monkeypatch.setattr(atendimento_repository, "buscar_mensagem", lambda db, i: mensagens.get(i))
    return chamado


class SessaoComData(SessaoFalsa):
    """Preenche criado_em no refresh, como o banco faria."""

    def refresh(self, objeto):
        super().refresh(objeto)
        if getattr(objeto, "criado_em", "") is None:
            objeto.criado_em = datetime.now(timezone.utc)


def pdf():
    return Arquivo(nome="comprovante.pdf", tipo="application/pdf", conteudo=PDF)


def test_cliente_anexa_sem_texto(banco):
    storage, db = StorageFalso(), SessaoFalsa()

    mensagem = atendimento.anexar_do_cliente(db, storage, cliente(), 3, pdf(), None)

    assert mensagem["anexo_nome"] == "comprovante.pdf" and mensagem["anexo_tamanho"] == len(PDF)
    assert mensagem["conteudo"] is None and mensagem["interna"] is False
    [(bucket, caminho)] = storage.enviados
    assert bucket == "anexos" and caminho.startswith("chamado-3/")


def test_chamado_de_outra_pessoa(banco):
    storage = StorageFalso()
    with pytest.raises(RecursoNaoEncontrado):
        atendimento.anexar_do_cliente(SessaoFalsa(), storage, cliente(ID_OUTRO), 3, pdf(), None)
    assert storage.enviados == {}


def test_chamado_concluido_nao_recebe_anexo(banco):
    banco.status = "concluido"
    with pytest.raises(RegraDeNegocio, match="não há reabertura"):
        atendimento.anexar_do_cliente(SessaoFalsa(), StorageFalso(), cliente(), 3, pdf(), None)


def test_falha_no_banco_apaga_o_anexo(banco):
    storage = StorageFalso()
    with pytest.raises(RuntimeError):
        atendimento.anexar_do_cliente(SessaoFalsa(erro_commit=RuntimeError("x")), storage, cliente(), 3, pdf(), None)
    assert storage.apagados == list(storage.enviados)


def test_link_temporario_do_anexo(banco):
    link = atendimento.anexo_para_o_cliente(SessaoFalsa(), StorageFalso(), cliente(), 3, 20)
    assert link["nome"] == "nota.pdf" and "token" in link["url"]


def test_cliente_nao_abre_anexo_interno(banco):
    with pytest.raises(RecursoNaoEncontrado):
        atendimento.anexo_para_o_cliente(SessaoFalsa(), StorageFalso(), cliente(), 3, 21)


def test_equipe_abre_anexo_interno(banco):
    link = atendimento.anexo_para_a_equipe(SessaoFalsa(), StorageFalso(), 3, 21)
    assert link["nome"] == "interno.pdf"


def test_equipe_anexa_mensagem_interna(banco):
    mensagem = atendimento.anexar_da_equipe(
        SessaoFalsa(), StorageFalso(), funcionario(nome="Ana"), 3, Arquivo("f.png", "image/png", PNG), "veja", True,
    )
    assert mensagem["interna"] is True and mensagem["da_equipe"] is True


def test_rota_de_anexo_do_cliente(api, banco):
    app.dependency_overrides[get_storage] = StorageFalso
    resposta = api(SessaoComData(), usuario=cliente()).post(
        "/chamados/3/anexos", files={"arquivo": ("nota.pdf", PDF, "application/pdf")}, data={"conteudo": "  "},
    )
    assert resposta.status_code == 201 and resposta.json()["conteudo"] is None


def test_rota_recusa_tipo_nao_aceito(api, banco):
    app.dependency_overrides[get_storage] = StorageFalso
    resposta = api(SessaoFalsa(), usuario=cliente()).post(
        "/chamados/3/anexos", files={"arquivo": ("x.exe", b"MZ" + b"0" * 10, "application/octet-stream")},
    )
    assert resposta.status_code == 422
