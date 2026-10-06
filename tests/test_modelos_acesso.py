from types import SimpleNamespace

import pytest

from src.models.contas import ModeloAcesso
from src.repositories import modelo_acesso_repository
from src.use_cases import modelos_acesso
from src.use_cases.erros import Conflito, RecursoNaoEncontrado, RegraDeNegocio
from tests.apoio import SessaoFalsa, api, funcionario  # noqa: F401 (api é fixture)

CODIGOS = ["atender_chamado", "gerenciar_contas", "gerenciar_modelos_acesso", "gerenciar_unidades",
           "movimentar_estoque"]


@pytest.fixture
def banco(monkeypatch):
    estado = SimpleNamespace(
        modelos={1: ModeloAcesso(id_modelo=1, nome="Admin", eh_admin=True, ativo=True),
                 2: ModeloAcesso(id_modelo=2, nome="Estoquista", eh_admin=False, ativo=True)},
        permissoes={2: ["movimentar_estoque"]},
        pessoas={1: 1, 2: 3},
    )
    r = modelo_acesso_repository
    monkeypatch.setattr(r, "listar_permissoes", lambda db: [SimpleNamespace(codigo=c, descricao=c) for c in CODIGOS])
    monkeypatch.setattr(r, "ids_das_permissoes", lambda db, cs: {c: CODIGOS.index(c) + 1 for c in cs if c in CODIGOS})
    monkeypatch.setattr(r, "listar_modelos", lambda db: list(estado.modelos.values()))
    monkeypatch.setattr(r, "buscar_modelo", lambda db, i: estado.modelos.get(i))
    monkeypatch.setattr(r, "modelo_por_nome", lambda db, n: next(
        (m for m in estado.modelos.values() if m.nome.lower() == n.lower()), None))
    monkeypatch.setattr(r, "codigos_por_modelo", lambda db: estado.permissoes)
    monkeypatch.setattr(r, "pessoas_por_modelo", lambda db: estado.pessoas)

    def trocar(db, id_modelo, ids):
        estado.permissoes[id_modelo] = sorted(CODIGOS[i - 1] for i in ids)
        if id_modelo not in estado.modelos:
            estado.modelos[id_modelo] = next(o for o in db.adicionados if getattr(o, "id_modelo", None) == id_modelo)

    monkeypatch.setattr(r, "trocar_permissoes", trocar)
    return estado


def test_lista_permissoes_marcando_as_da_gestao(banco):
    so_admin = {p["codigo"] for p in modelos_acesso.listar_permissoes(SessaoFalsa()) if p["so_admin"]}

    assert so_admin == {"gerenciar_contas", "gerenciar_modelos_acesso", "gerenciar_unidades"}


def test_admin_aparece_com_todas_as_permissoes(banco):
    admin, estoquista = modelos_acesso.listar_modelos(SessaoFalsa())

    assert admin["permissoes"] == CODIGOS
    assert (estoquista["permissoes"], estoquista["pessoas"]) == (["movimentar_estoque"], 3)


def test_cria_modelo_com_permissoes(banco):
    criado = modelos_acesso.criar_modelo(SessaoFalsa(primeiro_id=10), "Atendente", ["atender_chamado", "atender_chamado"])

    assert (criado["nome"], criado["permissoes"], criado["pessoas"]) == ("Atendente", ["atender_chamado"], 0)


def test_nome_repetido(banco):
    with pytest.raises(Conflito):
        modelos_acesso.criar_modelo(SessaoFalsa(), "estoquista", [])


def test_permissao_da_gestao_fora_do_admin(banco):
    with pytest.raises(RegraDeNegocio, match="Gestão são só do Admin: gerenciar_contas"):
        modelos_acesso.criar_modelo(SessaoFalsa(), "Gerente", ["gerenciar_contas", "movimentar_estoque"])


def test_permissao_desconhecida(banco):
    with pytest.raises(RegraDeNegocio, match="desconhecida: voar"):
        modelos_acesso.trocar_permissoes(SessaoFalsa(), 2, ["voar"])


def test_admin_nao_e_editavel(banco):
    with pytest.raises(RegraDeNegocio, match="Admin tem todas"):
        modelos_acesso.trocar_permissoes(SessaoFalsa(), 1, ["movimentar_estoque"])


def test_troca_permissoes_do_modelo(banco):
    modelo = modelos_acesso.trocar_permissoes(SessaoFalsa(), 2, ["atender_chamado", "movimentar_estoque"])

    assert modelo["permissoes"] == ["atender_chamado", "movimentar_estoque"]


def test_modelo_inexistente(banco):
    with pytest.raises(RecursoNaoEncontrado):
        modelos_acesso.alterar_modelo(SessaoFalsa(), 9, {"nome": "X"})


def test_rotas_exigem_permissao(api, banco):
    cliente = api(SessaoFalsa(), usuario=funcionario(), permitido=False)

    assert cliente.get("/modelos-acesso").status_code == 403
    assert cliente.get("/permissoes").status_code == 403


def test_listar_modelos_pela_rota(api, banco):
    resposta = api(SessaoFalsa(), usuario=funcionario()).get("/modelos-acesso")

    assert [m["nome"] for m in resposta.json()["items"]] == ["Admin", "Estoquista"]
