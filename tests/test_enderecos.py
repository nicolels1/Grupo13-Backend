import uuid

import pytest
from pydantic import ValidationError

from src.entities.enderecos import EnderecoCriar
from src.models.vendas import EnderecoCliente
from src.repositories import endereco_repository
from src.use_cases import enderecos
from src.use_cases.erros import RecursoNaoEncontrado
from tests.apoio import SessaoFalsa, api, funcionario  # noqa: F401

ID_CLIENTE = uuid.UUID("44444444-4444-4444-4444-444444444444")
ID_OUTRO = uuid.UUID("55555555-5555-5555-5555-555555555555")
ENDERECO = {"rua": "Rua A", "numero": "10", "bairro": "Centro", "cidade": "São Paulo", "uf": "sp", "cep": "01000-000"}


def cliente(id_usuario=ID_CLIENTE):
    return funcionario(id_usuario=id_usuario, tipo_conta="cliente", id_modelo_acesso=None)


class SessaoEnderecos(SessaoFalsa):
    def __init__(self):
        super().__init__()
        self.apagados = []

    def delete(self, objeto):
        self.apagados.append(objeto)


@pytest.fixture
def salvo(monkeypatch):
    endereco = EnderecoCliente(id_endereco=7, id_cliente=ID_CLIENTE, **{**ENDERECO, "uf": "SP", "cep": "01000000"})
    monkeypatch.setattr(endereco_repository, "buscar_endereco", lambda db, i: endereco if i == 7 else None)
    return endereco


def test_cep_e_uf_normalizados():
    dados = EnderecoCriar(**ENDERECO)
    assert (dados.cep, dados.uf) == ("01000000", "SP")


def test_cep_invalido():
    with pytest.raises(ValidationError, match="8 dígitos"):
        EnderecoCriar(**{**ENDERECO, "cep": "123"})


def test_criar_liga_ao_cliente():
    db = SessaoFalsa()
    endereco = enderecos.criar(db, cliente(), EnderecoCriar(**ENDERECO).model_dump())
    assert endereco.id_cliente == ID_CLIENTE and db.commits == 1


def test_endereco_de_outra_pessoa_nao_aparece(salvo):
    with pytest.raises(RecursoNaoEncontrado):
        enderecos.alterar(SessaoFalsa(), cliente(ID_OUTRO), 7, {"numero": "20"})


def test_cliente_apaga_o_proprio(salvo):
    db = SessaoEnderecos()
    enderecos.apagar(db, cliente(), 7)
    assert db.apagados == [salvo] and db.commits == 1


def test_rota_so_para_cliente(api):
    resposta = api(SessaoFalsa(), usuario=funcionario()).get("/enderecos")
    assert resposta.status_code == 403


def test_rota_apagar_responde_204(api, salvo):
    resposta = api(SessaoEnderecos(), usuario=cliente()).delete("/enderecos/7")
    assert resposta.status_code == 204
