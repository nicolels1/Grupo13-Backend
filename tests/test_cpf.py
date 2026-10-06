import pytest

from src.utils.cpf import cpf_valido, normalizar_cpf


def test_normalizar_tira_mascara():
    assert normalizar_cpf("529.982.247-25") == "52998224725"
    assert normalizar_cpf(" 529 982 247 25 ") == "52998224725"


@pytest.mark.parametrize("cpf", ["52998224725", "11144477735"])
def test_cpf_com_digitos_certos_e_valido(cpf):
    assert cpf_valido(cpf)


@pytest.mark.parametrize(
    "cpf",
    [
        pytest.param("52998224724", id="segundo-digito-errado"),
        pytest.param("52998224715", id="primeiro-digito-errado"),
        pytest.param("11111111111", id="sequencia-repetida"),
        pytest.param("5299822472", id="dez-digitos"),
        pytest.param("529982247255", id="doze-digitos"),
        pytest.param("5299822472a", id="com-letra"),
        pytest.param("", id="vazio"),
    ],
)
def test_cpf_invalido_e_recusado(cpf):
    assert not cpf_valido(cpf)
