from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.entities.transferencias import TransferenciaCriar
from src.models.estoque import ItemTransferencia, MovimentacaoEstoque, Transferencia
from src.repositories import estoque_repository, transferencia_repository, unidade_repository
from src.use_cases import transferencias
from src.use_cases.erros import RecursoNaoEncontrado, RegraDeNegocio
from tests.apoio import ID_FUNCIONARIO, SessaoComTrigger, SessaoFalsa, api, funcionario  # noqa: F401

LOJA_A, LOJA_B, CD, VARIANTE = 1, 2, 3, 10


class SessaoTransferencias(SessaoComTrigger):
    """Guarda transferências e itens adicionados, como o banco guardaria."""

    def __init__(self, banco):
        super().__init__(banco.saldos)
        self.banco = banco

    def flush(self):
        super().flush()
        for objeto in self.adicionados:
            if isinstance(objeto, Transferencia):
                self.banco.transferencias[objeto.id_transferencia] = objeto
            elif isinstance(objeto, ItemTransferencia) and objeto not in self.banco.itens:
                self.banco.itens.append(objeto)

    def commit(self):
        self.flush()
        super().commit()


@pytest.fixture
def banco(monkeypatch):
    estado = SimpleNamespace(
        saldos={},
        unidades={LOJA_A: SimpleNamespace(id_unidade=LOJA_A, tipo="loja", ativo=True),
                  LOJA_B: SimpleNamespace(id_unidade=LOJA_B, tipo="loja", ativo=True),
                  CD: SimpleNamespace(id_unidade=CD, tipo="cd", ativo=True)},
        transferencias={},
        itens=[],
    )
    monkeypatch.setattr(unidade_repository, "buscar_unidade", lambda db, u: estado.unidades.get(u))
    monkeypatch.setattr(estoque_repository, "buscar_variante", lambda db, v: object() if v == VARIANTE else None)
    monkeypatch.setattr(estoque_repository, "travar_estoque", lambda db, v, u, canais: {
        c: estado.saldos[(v, u, c)] for c in canais if (v, u, c) in estado.saldos})
    monkeypatch.setattr(transferencia_repository, "buscar_transferencia",
                        lambda db, i, travar=False: estado.transferencias.get(i))
    monkeypatch.setattr(transferencia_repository, "itens_da_transferencia",
                        lambda db, i: [x for x in estado.itens if x.id_transferencia == i])
    return estado


def com_estoque(banco, unidade, canal, quantidade, reservada=0):
    banco.saldos[(VARIANTE, unidade, canal)] = SimpleNamespace(quantidade=quantidade, quantidade_reservada=reservada)


def solicitar(banco, origem=LOJA_A, destino=LOJA_B, quantidade=3, canal_saida="loja_fisica", canal_entrada="loja_fisica"):
    dados = TransferenciaCriar(id_unidade_origem=origem, id_unidade_destino=destino, itens=[
        {"id_variante": VARIANTE, "canal_saida": canal_saida, "canal_entrada": canal_entrada, "quantidade": quantidade}
    ]).model_dump()
    return transferencias.solicitar(SessaoTransferencias(banco), ID_FUNCIONARIO, dados)


def movimentacoes(db):
    return [(m.tipo, m.id_unidade, m.quantidade) for m in db.adicionados if isinstance(m, MovimentacaoEstoque)]


# ---------- solicitação ----------

def test_solicitar_nao_mexe_no_estoque(banco):
    com_estoque(banco, LOJA_A, "loja_fisica", 5)

    transferencia = solicitar(banco)

    assert transferencia["status"] == "solicitada"
    assert transferencia["itens"][0].quantidade_solicitada == 3
    assert banco.saldos[(VARIANTE, LOJA_A, "loja_fisica")].quantidade == 5


def test_origem_igual_ao_destino():
    with pytest.raises(ValidationError, match="diferentes"):
        TransferenciaCriar(id_unidade_origem=1, id_unidade_destino=1, itens=[
            {"id_variante": 1, "canal_saida": "online", "canal_entrada": "online", "quantidade": 1}])


def test_cd_envia_do_online(banco):
    with pytest.raises(RegraDeNegocio, match="sai do canal online"):
        solicitar(banco, origem=CD, canal_saida="loja_fisica")


def test_cd_recebe_no_online(banco):
    with pytest.raises(RegraDeNegocio, match="CD só recebe"):
        solicitar(banco, destino=CD, canal_entrada="loja_fisica")


def test_unidade_desativada(banco):
    banco.unidades[LOJA_B].ativo = False

    with pytest.raises(RegraDeNegocio, match="destino desativada"):
        solicitar(banco)


# ---------- envio ----------

def test_envio_tira_da_origem(banco):
    com_estoque(banco, LOJA_A, "loja_fisica", 5)
    id_t = solicitar(banco)["id_transferencia"]
    db = SessaoTransferencias(banco)

    enviada = transferencias.enviar(db, id_t, ID_FUNCIONARIO, {"itens": []})

    assert enviada["status"] == "enviada" and enviada["id_enviado_por"] == ID_FUNCIONARIO
    assert movimentacoes(db) == [("saida_transferencia", LOJA_A, -3)]
    assert banco.saldos[(VARIANTE, LOJA_A, "loja_fisica")].quantidade == 2


def test_envio_parcial(banco):
    com_estoque(banco, LOJA_A, "loja_fisica", 5)
    t = solicitar(banco)
    item = t["itens"][0].id_item_transferencia
    db = SessaoTransferencias(banco)

    transferencias.enviar(db, t["id_transferencia"], ID_FUNCIONARIO, {"itens": [{"id_item_transferencia": item, "quantidade": 1}]})

    assert movimentacoes(db) == [("saida_transferencia", LOJA_A, -1)]


def test_envio_respeita_o_disponivel(banco):
    com_estoque(banco, LOJA_A, "loja_fisica", 3, reservada=0)
    com_estoque(banco, LOJA_A, "online", 3, reservada=2)
    id_t = solicitar(banco, canal_saida="online")["id_transferencia"]

    with pytest.raises(RegraDeNegocio, match="disponível insuficiente"):
        transferencias.enviar(SessaoTransferencias(banco), id_t, ID_FUNCIONARIO, {})


def test_nao_envia_duas_vezes(banco):
    com_estoque(banco, LOJA_A, "loja_fisica", 9)
    id_t = solicitar(banco)["id_transferencia"]
    transferencias.enviar(SessaoTransferencias(banco), id_t, ID_FUNCIONARIO, {})

    with pytest.raises(RegraDeNegocio, match="esta está enviada"):
        transferencias.enviar(SessaoTransferencias(banco), id_t, ID_FUNCIONARIO, {})


def test_enviar_zero_pecas(banco):
    t = solicitar(banco)
    item = t["itens"][0].id_item_transferencia

    with pytest.raises(RegraDeNegocio, match="cancele"):
        transferencias.enviar(SessaoTransferencias(banco), t["id_transferencia"], ID_FUNCIONARIO,
                              {"itens": [{"id_item_transferencia": item, "quantidade": 0}]})


# ---------- recebimento ----------

def enviada(banco, quantidade=3):
    com_estoque(banco, LOJA_A, "loja_fisica", 9)
    t = solicitar(banco, quantidade=quantidade)
    transferencias.enviar(SessaoTransferencias(banco), t["id_transferencia"], ID_FUNCIONARIO, {})
    return t["id_transferencia"], t["itens"][0].id_item_transferencia


def test_recebe_tudo(banco):
    id_t, _ = enviada(banco)
    db = SessaoTransferencias(banco)

    recebida = transferencias.receber(db, id_t, ID_FUNCIONARIO, {})

    assert recebida["status"] == "recebida"
    assert movimentacoes(db) == [("entrada_transferencia", LOJA_B, 3)]
    assert banco.saldos[(VARIANTE, LOJA_B, "loja_fisica")].quantidade == 3


def test_diferenca_vira_perda_com_motivo(banco):
    id_t, item = enviada(banco)
    db = SessaoTransferencias(banco)

    transferencias.receber(db, id_t, ID_FUNCIONARIO, {
        "itens": [{"id_item_transferencia": item, "quantidade": 2}], "motivo_diferenca": "caixa rasgada",
        "tipo_diferenca": "avaria",
    })

    assert movimentacoes(db) == [("entrada_transferencia", LOJA_B, 3), ("avaria", LOJA_B, -1)]
    assert banco.saldos[(VARIANTE, LOJA_B, "loja_fisica")].quantidade == 2


def test_diferenca_sem_motivo(banco):
    id_t, item = enviada(banco)

    with pytest.raises(RegraDeNegocio, match="motivo"):
        transferencias.receber(SessaoTransferencias(banco), id_t, ID_FUNCIONARIO,
                               {"itens": [{"id_item_transferencia": item, "quantidade": 1}]})


def test_recebida_maior_que_enviada(banco):
    id_t, item = enviada(banco)

    with pytest.raises(RegraDeNegocio, match="maior que a enviada"):
        transferencias.receber(SessaoTransferencias(banco), id_t, ID_FUNCIONARIO,
                               {"itens": [{"id_item_transferencia": item, "quantidade": 4}]})


def test_item_de_outra_transferencia(banco):
    id_t, _ = enviada(banco)

    with pytest.raises(RegraDeNegocio, match="não é desta transferência"):
        transferencias.receber(SessaoTransferencias(banco), id_t, ID_FUNCIONARIO,
                               {"itens": [{"id_item_transferencia": 999, "quantidade": 1}]})


# ---------- cancelamento ----------

def test_cancela_antes_do_envio(banco):
    id_t = solicitar(banco)["id_transferencia"]

    cancelada = transferencias.cancelar(SessaoTransferencias(banco), id_t, ID_FUNCIONARIO, "pedido errado")

    assert (cancelada["status"], cancelada["motivo_cancelamento"], cancelada["id_cancelado_por"]) == (
        "cancelada", "pedido errado", ID_FUNCIONARIO)


def test_nao_cancela_depois_de_enviada(banco):
    id_t, _ = enviada(banco)

    with pytest.raises(RegraDeNegocio, match="cancelar"):
        transferencias.cancelar(SessaoTransferencias(banco), id_t, ID_FUNCIONARIO, "desisti")


def test_transferencia_inexistente(banco):
    with pytest.raises(RecursoNaoEncontrado):
        transferencias.cancelar(SessaoTransferencias(banco), 999, ID_FUNCIONARIO, "x")


# ---------- rotas ----------

def test_cancelar_exige_motivo(api, banco):
    resposta = api(SessaoFalsa(), usuario=funcionario()).post("/transferencias/1/cancelar", json={"motivo": ""})

    assert resposta.status_code == 422


def test_solicitar_sem_permissao(api, banco):
    corpo = {"id_unidade_origem": LOJA_A, "id_unidade_destino": LOJA_B, "itens": [
        {"id_variante": VARIANTE, "canal_saida": "online", "canal_entrada": "online", "quantidade": 1}]}

    assert api(SessaoFalsa(), usuario=funcionario(), permitido=False).post("/transferencias", json=corpo).status_code == 403


def test_listar_exige_permissao_de_transferencia(api, banco):
    assert api(SessaoFalsa(), usuario=funcionario(), permitido=False).get("/transferencias").status_code == 403


def test_disponivel_conferido_pelo_total_da_variante(banco):
    # dois itens da mesma variante e canal de saída: 2 + 2 > 3 disponíveis
    com_estoque(banco, LOJA_A, "loja_fisica", 3)
    dados = TransferenciaCriar(id_unidade_origem=LOJA_A, id_unidade_destino=LOJA_B, itens=[
        {"id_variante": VARIANTE, "canal_saida": "loja_fisica", "canal_entrada": "loja_fisica", "quantidade": 2},
        {"id_variante": VARIANTE, "canal_saida": "loja_fisica", "canal_entrada": "online", "quantidade": 2},
    ]).model_dump()
    id_t = transferencias.solicitar(SessaoTransferencias(banco), ID_FUNCIONARIO, dados)["id_transferencia"]

    with pytest.raises(RegraDeNegocio, match="disponível insuficiente"):
        transferencias.enviar(SessaoTransferencias(banco), id_t, ID_FUNCIONARIO, {})
