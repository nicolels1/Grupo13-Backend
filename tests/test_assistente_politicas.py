from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from src.app import app
from src.config import settings
from src.database.session import get_db
from src.models.fidelidade import RegraFidelidade
from src.repositories import fidelidade_repository
from src.use_cases import assistente_politicas, pedidos
from tests.apoio import SessaoFalsa

CHAVE = "chave-de-teste"


def regra(marca="lorenzi", **campos):
    valores = {"valor_ponto": Decimal("0.0500"), "pontos_por_real": Decimal("1.00"),
               "multiplicador_online": Decimal("2.00"), "bonus_ouro": Decimal("0.50"), "validade_meses": 12,
               "validade_meses_ouro": 18, "limite_prata": 500, "limite_ouro": 1000, "resgate_minimo": 100}
    return RegraFidelidade(marca=marca, **(valores | campos))


@pytest.fixture
def regras(monkeypatch):
    vigentes = {"lorenzi": regra(), "vulto": regra("vulto", multiplicador_online=Decimal("1.00"),
                                                   bonus_ouro=Decimal("0"), validade_meses=None,
                                                   validade_meses_ouro=None)}
    monkeypatch.setattr(fidelidade_repository, "regra_vigente", lambda db, marca, hoje: vigentes.get(marca))
    return vigentes


def test_pontos_de_cada_marca_pela_regra_vigente(regras):
    lorenzi, vulto = assistente_politicas.politicas(SessaoFalsa())["pontos"]

    assert (lorenzi["marca"], lorenzi["resgate_minimo"]) == ("lorenzi", 100)
    assert lorenzi["texto"] == (
        "Lorenzi: cada R$ 1 em compras dá 1 ponto, multiplicado por 2 nas compras pelo site ou pelo app. Cada ponto "
        "ganho hoje vale R$ 0,05 no resgate. Na categoria Ouro, os pontos ganhos têm mais 50%. Os pontos vencem 12 "
        "meses depois de ganhos (18 meses na categoria Ouro). A categoria sobe pelos pontos ganhos no ano: Prata a "
        "partir de 500 e Ouro a partir de 1.000. Dá para usar a partir de 100 pontos, no pagamento do site ou no caixa "
        "da loja, com desconto menor que o valor das peças. Os pontos da Lorenzi só valem na Lorenzi."
    )
    # sem multiplicador, sem bônus e sem validade, o texto não promete nada disso
    assert "multiplicado" not in vulto["texto"] and "Ouro, os pontos" not in vulto["texto"]
    assert "Os pontos ganhos hoje ainda não têm prazo de vencimento." in vulto["texto"]
    assert "nunca" not in vulto["texto"]


def test_valor_do_ponto_com_mais_casas_nao_arredonda(regras):
    regras["lorenzi"] = regra(valor_ponto=Decimal("0.0125"))

    [lorenzi, _] = assistente_politicas.politicas(SessaoFalsa())["pontos"]

    assert "vale R$ 0,0125 no resgate" in lorenzi["texto"]


def test_marca_sem_regra_fica_de_fora(regras):
    del regras["vulto"]
    assert [r["marca"] for r in assistente_politicas.politicas(SessaoFalsa())["pontos"]] == ["lorenzi"]


# o texto sai da constante que a regra usa: mudou o frete ou a reserva, a resposta muda junto
def test_entrega_vem_das_constantes_do_pedido(regras, monkeypatch):
    monkeypatch.setattr(pedidos, "FRETE_ENTREGA", Decimal("24.90"))
    monkeypatch.setattr(pedidos, "RESERVA", timedelta(minutes=20))

    entrega = assistente_politicas.politicas(SessaoFalsa())["entrega"]

    assert "frete de R$ 24,90, grátis quando as peças somam a partir de R$ 299,00" in entrega
    assert "reservadas por 20 minutos" in entrega and "por 7 dias" in entrega


def test_troca_so_numa_loja_da_marca_e_pagamento_em_dinheiro_so_na_loja(regras):
    resposta = assistente_politicas.politicas(SessaoFalsa())

    assert "até 30 dias depois da entrega, numa loja da mesma marca da peça" in resposta["troca_devolucao"]
    assert resposta["pagamento"].startswith(
        "No site, o pagamento é por Pix, cartão de crédito ou cartão de débito. Na loja, também dá para pagar em "
        "dinheiro."
    )


def test_rota_das_politicas(regras, monkeypatch):
    monkeypatch.setattr(settings, "ASSISTENTE_CHAVE", CHAVE)
    app.dependency_overrides[get_db] = lambda: SessaoFalsa()
    try:
        resposta = TestClient(app).get("/assistente/politicas", headers={"X-Assistente-Chave": CHAVE})
    finally:
        app.dependency_overrides.clear()

    assert resposta.status_code == 200
    assert set(resposta.json()) == {"pontos", "entrega", "troca_devolucao", "pagamento", "conta"}
