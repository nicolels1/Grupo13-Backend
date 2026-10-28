from datetime import date, datetime, time, timezone

import pytest

from src.models.horarios import ExcecaoHorario, HorarioUnidade
from src.use_cases import horarios
from src.use_cases.horarios import BRASILIA

# loja de rua: seg–sex 10h–20h, sábado 10h–18h, domingo fechada
RUA = [HorarioUnidade(id_unidade=1, dia_semana=d, abre=time(10), fecha=time(20)) for d in range(5)] + [
    HorarioUnidade(id_unidade=1, dia_semana=5, abre=time(10), fecha=time(18))
]


def em(ano, mes, dia, hora, minuto=0):
    return datetime(ano, mes, dia, hora, minuto, tzinfo=BRASILIA)


@pytest.mark.parametrize("hora, minuto, esperado", [(10, 0, "10h"), (22, 30, "22h30"), (9, 5, "9h05")])
def test_formata_hora_sem_zeros(hora, minuto, esperado):
    assert horarios.formatar_hora(time(hora, minuto)) == esperado


@pytest.mark.parametrize("hora, aberta, texto", [
    (9, False, "Hoje a loja abre das 10h às 20h."),
    (10, True, "Hoje a loja está aberta até as 20h."),
    (19, True, "Hoje a loja está aberta até as 20h."),
    (20, False, "Hoje a loja já fechou; abriu das 10h às 20h."),
])
def test_dia_de_semana_conforme_a_hora(hora, aberta, texto):
    # 28/10/2026 é uma quarta
    assert horarios.situacao_de_hoje(RUA, None, em(2026, 10, 28, hora)) == {"aberta_agora": aberta, "texto": texto}


def test_dia_sem_horario_e_fechado():
    # 01/11/2026 é um domingo
    resposta = horarios.situacao_de_hoje(RUA, None, em(2026, 11, 1, 12))
    assert resposta == {"aberta_agora": False, "texto": "Hoje a loja fica fechada."}


def test_excecao_fechada_vale_mais_que_a_semana():
    natal = ExcecaoHorario(id_unidade=1, data=date(2026, 12, 25), abre=None, fecha=None, motivo="Natal")
    resposta = horarios.situacao_de_hoje(RUA, natal, em(2026, 12, 25, 12))
    # com a exceção cadastrada, não precisa do aviso de feriado
    assert resposta == {"aberta_agora": False, "texto": "Hoje (Natal) a loja fica fechada."}


def test_excecao_com_horario_especial():
    vespera = ExcecaoHorario(id_unidade=1, data=date(2026, 12, 24), abre=time(10), fecha=time(18), motivo="véspera de Natal")
    resposta = horarios.situacao_de_hoje(RUA, vespera, em(2026, 12, 24, 19))
    assert resposta["texto"] == "Hoje (véspera de Natal) a loja já fechou; abriu das 10h às 18h."


def test_feriado_nacional_sem_excecao_avisa_que_pode_mudar():
    # 20/11/2026 é uma sexta, feriado da Consciência Negra
    resposta = horarios.situacao_de_hoje(RUA, None, em(2026, 11, 20, 12))
    assert resposta["aberta_agora"] is True
    assert resposta["texto"].endswith(horarios.AVISO_FERIADO)


def test_usa_o_horario_de_brasilia():
    # 23h30 em UTC ainda é 20h30 em Brasília: quarta, já fechou
    agora = datetime(2026, 10, 28, 23, 30, tzinfo=timezone.utc)
    assert horarios.situacao_de_hoje(RUA, None, agora)["texto"].startswith("Hoje a loja já fechou")


def test_semana_lista_os_sete_dias():
    dias = horarios.semana(RUA)
    assert [d["dia"] for d in dias] == horarios.DIAS_DA_SEMANA
    assert dias[0]["horario"] == "10h às 20h"
    assert dias[5]["horario"] == "10h às 18h"
    assert dias[6]["horario"] == "fechada"
