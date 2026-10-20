from src.models.migracao import OrigemMigracao
from src.repositories import migracao_repository
from tests.apoio import SessaoFalsa, api, funcionario  # noqa: F401 (api é fixture)


# pedido da Vulto que veio de duas linhas (a cópia, V5) e teve o valor corrigido (V1)
def origem_do_pedido():
    return [
        OrigemMigracao(fonte="vulto.db", linha_origem="vendas#272"),
        OrigemMigracao(fonte="vulto.db", linha_origem="vendas#273", decisao="V5"),
        OrigemMigracao(fonte="vulto.db", linha_origem="vendas#272", campo="valor_total", valor_original="18990",
                       decisao="V1"),
    ]


def test_origem_de_um_registro_migrado(api, monkeypatch):
    pedidos = []
    monkeypatch.setattr(migracao_repository, "origem",
                        lambda db, tabela, id_registro: pedidos.append((tabela, id_registro)) or origem_do_pedido())

    resposta = api(SessaoFalsa(), usuario=funcionario()).get(
        "/migracao/origem", params={"tabela": "pedido", "id_registro": "123"})

    assert resposta.status_code == 200 and pedidos == [("pedido", "123")]
    assert [o["decisao"] for o in resposta.json()["items"]] == [None, "V5", "V1"]
    assert resposta.json()["items"][2]["valor_original"] == "18990"


def test_registro_que_nao_veio_da_migracao(api, monkeypatch):
    monkeypatch.setattr(migracao_repository, "origem", lambda db, tabela, id_registro: [])

    resposta = api(SessaoFalsa(), usuario=funcionario()).get(
        "/migracao/origem", params={"tabela": "pedido", "id_registro": "1"})

    assert resposta.json() == {"items": []}


def test_tabela_so_com_letras_minusculas(api):
    resposta = api(SessaoFalsa(), usuario=funcionario()).get(
        "/migracao/origem", params={"tabela": "pedido; drop", "id_registro": "1"})

    assert resposta.status_code == 422


def test_origem_exige_permissao(api):
    resposta = api(SessaoFalsa(), usuario=funcionario(), permitido=False).get(
        "/migracao/origem", params={"tabela": "pedido", "id_registro": "1"})

    assert resposta.status_code == 403
