from datetime import date, time

import pytest

from scripts import carregar_horarios
from src.models.estoque import Unidade


def loja(nome="Loja Pinheiros", complemento=None):
    return Unidade(nome=nome, rua="Rua dos Pinheiros", complemento=complemento, bairro="Pinheiros")


@pytest.mark.parametrize("unidade, shopping", [
    (loja(), False),
    (loja(complemento="Shopping Eldorado, piso 2"), True),
    (loja(nome="Vulto SHOPPING Eldorado"), True),
])
def test_reconhece_loja_de_shopping(unidade, shopping):
    assert carregar_horarios.eh_shopping(unidade) is shopping


def test_rua_fecha_domingo_e_shopping_abre_a_tarde():
    assert 6 not in carregar_horarios.horarios_da_semana(False)
    assert carregar_horarios.horarios_da_semana(True)[6] == (time(14), time(20))


def test_excecoes_dos_proximos_doze_meses():
    hoje = date(2026, 10, 28)
    rua = {dia: (abre, fecha, motivo) for dia, abre, fecha, motivo in carregar_horarios.excecoes(False, hoje)}
    shopping = {dia: (abre, fecha, motivo) for dia, abre, fecha, motivo in carregar_horarios.excecoes(True, hoje)}

    assert rua[date(2026, 11, 2)] == (None, None, "Finados")
    assert shopping[date(2026, 11, 2)] == (time(14), time(20), "Finados")
    assert shopping[date(2026, 12, 25)] == (None, None, "Natal")  # Natal e Ano-Novo: todas fechadas
    assert shopping[date(2027, 1, 1)] == (None, None, "Ano-Novo")
    assert shopping[date(2026, 12, 24)] == (time(10), time(18), "véspera de Natal")
    assert date(2026, 12, 24) not in rua
    assert max(rua) <= date(2027, 10, 28)
    assert date(2026, 10, 12) not in rua  # já passou
