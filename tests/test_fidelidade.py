from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from src.entities.fidelidade import RegraCriar
from src.models.fidelidade import RegraFidelidade
from src.repositories import fidelidade_repository
from src.use_cases import fidelidade
from src.use_cases.erros import RegraDeNegocio
from tests.apoio import ID_FUNCIONARIO, SessaoFalsa, api, funcionario  # noqa: F401 (api é fixture)


def sql(consulta):
    return str(consulta.compile(dialect=postgresql.dialect()))


# o passivo soma o que sobra em cada lote pelo valor do ponto da época (K3), separado por marca (P10)
def test_passivo_por_marca_pelo_valor_da_epoca():
    consulta = sql(fidelidade_repository.consulta_passivo())

    assert "sum(lote_pontos.restante * lote_pontos.valor_ponto)" in consulta
    assert "WHERE lote_pontos.restante > " in consulta
    assert "GROUP BY lote_pontos.marca" in consulta


# ---------- regras (P10) ----------

def regra(id_regra, marca, valida_desde, **campos):
    return RegraFidelidade(
        id_regra=id_regra, marca=marca, valida_desde=valida_desde, valor_ponto=Decimal("0.05"),
        pontos_por_real=Decimal("1"), multiplicador_online=Decimal("2"), bonus_ouro=Decimal("0.5"), validade_meses=12,
        validade_meses_ouro=18, limite_prata=2000, limite_ouro=5000, resgate_minimo=100, id_criado_por=None,
        criado_em=datetime(2026, 10, 1, tzinfo=timezone.utc), **campos,
    )


NOVA = {"marca": "lorenzi", "valida_desde": "2026-11-01", "valor_ponto": "0.04", "pontos_por_real": "1",
        "multiplicador_online": "2", "bonus_ouro": "0.5", "validade_meses": 12, "validade_meses_ouro": 18,
        "limite_prata": 2000, "limite_ouro": 5000, "resgate_minimo": 100}


@pytest.fixture
def regras(monkeypatch):
    guardadas = [regra(4, "lorenzi", date(2026, 10, 1)), regra(3, "vulto", date(2026, 10, 1)),
                 regra(2, "vulto", date(2025, 3, 1)), regra(1, "vulto", date(2025, 1, 1))]
    monkeypatch.setattr(fidelidade, "hoje", lambda: date(2026, 10, 20))

    def listar(db, marca=None):
        ordem = sorted(guardadas, key=lambda r: (r.marca, -r.valida_desde.toordinal(), -r.id_regra))
        return [r for r in ordem if marca in (None, r.marca)]

    monkeypatch.setattr(fidelidade_repository, "listar_regras", listar)
    return guardadas


def test_vigente_e_a_mais_nova_ate_hoje_de_cada_marca(regras):
    regras.append(regra(5, "vulto", date(2026, 12, 1)))  # futura: ainda não vale

    vigentes = {(r["marca"], r["id_regra"]) for r in fidelidade.listar_regras(SessaoFalsa()) if r["vigente"]}

    assert vigentes == {("lorenzi", 4), ("vulto", 3)}


def test_regra_nova_vale_dali_em_diante_e_guarda_quem_criou(regras):
    db = SessaoFalsa(primeiro_id=6)
    dados = RegraCriar(**NOVA).model_dump()
    db.refresh = lambda r: (setattr(r, "id_regra", 6), setattr(r, "criado_em", datetime.now(timezone.utc)),
                            regras.append(r))

    criada = fidelidade.criar_regra(db, ID_FUNCIONARIO, dados)

    assert (criada["id_regra"], criada["id_criado_por"], criada["vigente"], db.commits) == (6, ID_FUNCIONARIO, False, 1)


@pytest.mark.parametrize("dia", ["2026-10-20", "2026-09-01"])
def test_regra_nova_nunca_vale_para_hoje_nem_para_tras(regras, dia):
    with pytest.raises(RegraDeNegocio, match="amanhã"):
        fidelidade.criar_regra(SessaoFalsa(), ID_FUNCIONARIO, RegraCriar(**{**NOVA, "valida_desde": dia}).model_dump())


@pytest.mark.parametrize("campos, erro", [
    ({"validade_meses_ouro": None}, "validade das duas"),
    ({"validade_meses_ouro": 6}, "validade no Ouro"),
    ({"limite_prata": 5000}, "limite do Ouro"),
])
def test_regra_incoerente_e_recusada(campos, erro):
    with pytest.raises(ValidationError, match=erro):
        RegraCriar(**{**NOVA, **campos})


def test_regras_exigem_gerenciar_fidelidade(api, regras):
    assert api(SessaoFalsa(), usuario=funcionario(), permitido=False).get("/fidelidade/regras").status_code == 403
    resposta = api(SessaoFalsa(), usuario=funcionario()).get("/fidelidade/regras?marca=vulto")
    assert resposta.status_code == 200 and [r["id_regra"] for r in resposta.json()["items"]] == [3, 2, 1]
