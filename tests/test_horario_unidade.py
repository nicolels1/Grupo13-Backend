from datetime import date, time
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.entities.unidades import ExcecaoHorarioSalvar, HorarioSemana
from src.models.estoque import Unidade
from src.models.horarios import ExcecaoHorario, HorarioUnidade
from src.repositories import horario_repository, unidade_repository
from src.use_cases import horarios
from src.use_cases.erros import RecursoNaoEncontrado, RegraDeNegocio
from tests.apoio import SessaoFalsa, api, funcionario  # noqa: F401 — api é fixture

HOJE = date(2026, 10, 28)


@pytest.fixture
def banco(monkeypatch):
    estado = SimpleNamespace(
        unidades={2: Unidade(id_unidade=2, nome="Loja Pinheiros", tipo="loja"),
                  1: Unidade(id_unidade=1, nome="CD Guarulhos", tipo="cd")},
        semana=[HorarioUnidade(id_unidade=2, dia_semana=0, abre=time(10), fecha=time(20))],
        excecoes={},
    )

    def trocar(db, id_unidade, dias):
        estado.semana = dias

    m = monkeypatch.setattr
    m(unidade_repository, "buscar_unidade", lambda db, i: estado.unidades.get(i))
    m(horario_repository, "semana", lambda db, i: estado.semana)
    m(horario_repository, "trocar_semana", trocar)
    m(horario_repository, "excecoes_a_partir", lambda db, i, dia: sorted(
        (e for e in estado.excecoes.values() if e.data >= dia), key=lambda e: e.data))
    m(horario_repository, "buscar_excecao", lambda db, i, dia: estado.excecoes.get(dia))
    return estado


# ---------- o que entra ----------

def test_semana_ordena_os_dias():
    semana = HorarioSemana(semana=[{"dia_semana": 5, "abre": "10:00", "fecha": "18:00"},
                                   {"dia_semana": 0, "abre": "10:00", "fecha": "20:00"}])
    assert [d.dia_semana for d in semana.semana] == [0, 5]


@pytest.mark.parametrize("dias", [
    [{"dia_semana": 0, "abre": "10:00", "fecha": "20:00"}, {"dia_semana": 0, "abre": "11:00", "fecha": "19:00"}],
    [{"dia_semana": 1, "abre": "20:00", "fecha": "10:00"}],
    [{"dia_semana": 7, "abre": "10:00", "fecha": "20:00"}],
])
def test_semana_invalida(dias):
    with pytest.raises(ValidationError):
        HorarioSemana(semana=dias)


def test_excecao_sem_horario_e_dia_fechado():
    assert ExcecaoHorarioSalvar(motivo="  Natal ").model_dump() == {"abre": None, "fecha": None, "motivo": "Natal"}


@pytest.mark.parametrize("dados", [
    {"abre": "10:00", "motivo": "reforma"},
    {"abre": "18:00", "fecha": "10:00", "motivo": "reforma"},
    {"motivo": "x"},
])
def test_excecao_invalida(dados):
    with pytest.raises(ValidationError):
        ExcecaoHorarioSalvar(**dados)


# ---------- regras ----------

def test_cd_nao_tem_horario(banco):
    with pytest.raises(RegraDeNegocio, match="Só loja"):
        horarios.ver_horario(SessaoFalsa(), 1, HOJE)


def test_unidade_inexistente(banco):
    with pytest.raises(RecursoNaoEncontrado):
        horarios.ver_horario(SessaoFalsa(), 99, HOJE)


def test_salvar_semana_troca_a_semana_inteira(banco):
    db = SessaoFalsa()
    dias = [{"dia_semana": 5, "abre": time(10), "fecha": time(18)}]

    resposta = horarios.salvar_semana(db, 2, dias, HOJE)

    assert [(d.id_unidade, d.dia_semana) for d in resposta["semana"]] == [(2, 5)]
    assert db.commits == 1


def test_cria_excecao_e_depois_altera(banco):
    db = SessaoFalsa()
    natal = date(2026, 12, 25)

    horarios.salvar_excecao(db, 2, natal, {"abre": None, "fecha": None, "motivo": "Natal"}, HOJE)
    [criada] = db.adicionados
    assert (criada.id_unidade, criada.data, criada.abre) == (2, natal, None)

    banco.excecoes[natal] = criada
    horarios.salvar_excecao(db, 2, natal, {"abre": time(14), "fecha": time(18), "motivo": "Natal"}, HOJE)
    assert (criada.abre, criada.fecha) == (time(14), time(18))
    assert len(db.adicionados) == 1 and db.commits == 2


def test_data_que_ja_passou_nao_muda(banco):
    with pytest.raises(RegraDeNegocio, match="já passou"):
        horarios.salvar_excecao(SessaoFalsa(), 2, date(2026, 10, 27), {"abre": None, "fecha": None, "motivo": "x"}, HOJE)


def test_ver_horario_mostra_so_excecoes_de_hoje_em_diante(banco):
    for dia in (date(2026, 10, 1), date(2026, 11, 2)):
        banco.excecoes[dia] = ExcecaoHorario(id_unidade=2, data=dia, motivo="teste")
    assert [e.data for e in horarios.ver_horario(SessaoFalsa(), 2, HOJE)["excecoes"]] == [date(2026, 11, 2)]


def test_remover_excecao(banco):
    db = SessaoFalsa()
    natal = ExcecaoHorario(id_unidade=2, data=date(2026, 12, 25), motivo="Natal")
    banco.excecoes[natal.data] = natal

    horarios.remover_excecao(db, 2, natal.data)

    assert db.apagados == [natal] and db.commits == 1
    with pytest.raises(RecursoNaoEncontrado):
        horarios.remover_excecao(db, 2, date(2027, 1, 1))


# ---------- rotas ----------

SEMANA = {"semana": [{"dia_semana": 0, "abre": "10:00", "fecha": "20:00"}]}


def test_horario_e_publico(banco, api):
    resposta = api(SessaoFalsa()).get("/unidades/2/horario")

    assert resposta.status_code == 200
    assert resposta.json()["semana"] == [{"dia_semana": 0, "abre": "10:00:00", "fecha": "20:00:00"}]


def test_editar_exige_login_e_permissao(banco, api):
    assert api(SessaoFalsa()).put("/unidades/2/horario", json=SEMANA).status_code == 401
    sem_permissao = api(SessaoFalsa(), usuario=funcionario(), permitido=False)
    assert sem_permissao.put("/unidades/2/horario", json=SEMANA).status_code == 403
    assert sem_permissao.delete("/unidades/2/excecoes/2026-12-25").status_code == 403


def test_admin_salva_a_semana_e_a_excecao(banco, api):
    cliente = api(SessaoFalsa(), usuario=funcionario())

    assert cliente.put("/unidades/2/horario", json=SEMANA).status_code == 200
    resposta = cliente.put("/unidades/2/excecoes/2099-12-25", json={"motivo": "Natal"})
    assert resposta.status_code == 200


def test_cd_responde_422(banco, api):
    assert api(SessaoFalsa(), usuario=funcionario()).put("/unidades/1/horario", json=SEMANA).status_code == 422
