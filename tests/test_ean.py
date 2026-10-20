import pytest

from src.utils.ean import ean_interno, ean_valido


# EANs reais do catálogo da Vulto
@pytest.mark.parametrize("ean", ["7897969295881", "7894105009227"])
def test_ean_com_digito_certo_e_valido(ean):
    assert ean_valido(ean)


@pytest.mark.parametrize(
    "ean",
    [
        pytest.param("7890231000000", id="cortado-pelo-excel"),
        pytest.param("7897969295882", id="digito-errado"),
        pytest.param("789796929588", id="12-digitos"),
        pytest.param("789796929588A", id="com-letra"),
    ],
)
def test_ean_invalido(ean):
    assert not ean_valido(ean)


def test_codigo_interno_comeca_com_20_e_e_valido():
    ean = ean_interno(42)

    assert (ean[:2], ean[2:12], ean_valido(ean)) == ("20", "0000000042", True)


def test_codigo_interno_e_diferente_por_variante():
    assert ean_interno(1) != ean_interno(2)
