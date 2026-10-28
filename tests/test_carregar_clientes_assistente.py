from scripts import carregar_clientes_assistente as clientes_teste
from scripts import carregar_demo as demo
from src.utils.cpf import cpf_valido


def test_apelido_entra_antes_do_arroba():
    assert clientes_teste.email_com_apelido(" caixa.banca@gmail.com", "ana") == "caixa.banca+ana@gmail.com"


def test_cpfs_validos_e_diferentes_dos_da_demonstracao():
    cpfs = [cpf for _, _, cpf, _ in clientes_teste.CLIENTES_TESTE]
    demonstracao = {c["cpf"] for c in demo.CLIENTES} | {demo.CPF_NA_NOTA}

    assert all(cpf_valido(cpf) for cpf in cpfs)
    assert len(set(cpfs)) == len(cpfs) and not set(cpfs) & demonstracao


def test_clientes_de_teste_tem_pedidos_nas_duas_marcas():
    marcas = {marca for *_, compras in clientes_teste.CLIENTES_TESTE for marca, _ in compras}
    assert marcas == {"lorenzi", "vulto"}


def test_cpf_formatado():
    assert clientes_teste.cpf_formatado("12345678901") == "123.456.789-01"
