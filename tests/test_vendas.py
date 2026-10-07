import uuid
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.entities.vendas import VendaFisica
from src.models.contas import Usuario
from src.models.estoque import MovimentacaoEstoque
from src.repositories import permissao_repository, pedido_repository, usuario_repository
from src.routes.contas import get_supabase_admin
from src.app import app
from src.use_cases import clientes, pedidos, vendas
from src.use_cases.erros import Conflito, RecursoNaoEncontrado, RegraDeNegocio
from src.utils.supabase_admin import ErroSupabase
from tests.apoio import ID_FUNCIONARIO, SessaoFalsa, api, funcionario  # noqa: F401
from tests.apoio_vendas import (  # noqa: F401
    CAMISA, CD, ID_CLIENTE, LOJA_SP, SessaoVendas, banco, cliente, com_estoque, saldo,
)
from tests.test_pedidos import entrega, pedido_pago, retirada

CPF = "52998224725"
CPF_CERTO = "39053344705"


def venda(**campos):
    dados = dict(id_unidade=LOJA_SP, itens=[{"id_variante": CAMISA, "quantidade": 2}],
                 pagamentos=[{"metodo": "dinheiro", "valor": "150.00"}, {"metodo": "pix", "valor": "50.00"}])
    dados.update(campos)
    return VendaFisica(**dados).model_dump()


def movimentacoes(db):
    return [(m.tipo, m.canal, m.quantidade) for m in db.adicionados if isinstance(m, MovimentacaoEstoque)]


# ---------- venda física ----------

def test_venda_fisica_nasce_paga_e_entregue(banco):
    com_estoque(banco, CAMISA, LOJA_SP, "loja_fisica", 5)
    db = SessaoVendas(banco)

    pedido = vendas.registrar_venda_fisica(db, funcionario(), venda())

    assert pedido["status"] == "entregue" and pedido["canal"] == "loja_fisica" and pedido["modalidade"] is None
    assert pedido["id_registrado_por"] == ID_FUNCIONARIO and pedido["id_cliente"] is None
    assert pedido["valor_total"] == Decimal("200.00") and pedido["valor_pago"] == Decimal("200.00")
    assert [p.id_transacao_gateway is None for p in pedido["pagamentos"]] == [True, False]
    assert movimentacoes(db) == [("venda", "loja_fisica", -2)]
    assert saldo(banco, CAMISA, LOJA_SP, "loja_fisica") == (3, 0)


def test_venda_fisica_com_cpf_liga_ao_cliente(banco):
    com_estoque(banco, CAMISA, LOJA_SP, "loja_fisica", 5)
    banco.clientes_por_cpf[CPF] = SimpleNamespace(id_usuario=ID_CLIENTE)
    pedido = vendas.registrar_venda_fisica(SessaoVendas(banco), funcionario(), venda(cpf_cliente="529.982.247-25"))
    assert pedido["id_cliente"] == ID_CLIENTE


def test_cpf_sem_cadastro(banco):
    with pytest.raises(RecursoNaoEncontrado, match="cadastre"):
        vendas.registrar_venda_fisica(SessaoVendas(banco), funcionario(), venda(cpf_cliente=CPF))


def test_pagamentos_precisam_fechar_o_total(banco):
    com_estoque(banco, CAMISA, LOJA_SP, "loja_fisica", 5)
    with pytest.raises(RegraDeNegocio, match="somam R\\$ 150.00"):
        vendas.registrar_venda_fisica(SessaoVendas(banco), funcionario(),
                                      venda(pagamentos=[{"metodo": "dinheiro", "valor": "150.00"}]))


def test_venda_fisica_usa_o_estoque_da_loja_fisica(banco):
    com_estoque(banco, CAMISA, LOJA_SP, "loja_fisica", 1)
    com_estoque(banco, CAMISA, LOJA_SP, "online", 10)
    with pytest.raises(RegraDeNegocio, match="disponível insuficiente"):
        vendas.registrar_venda_fisica(SessaoVendas(banco), funcionario(), venda())


def test_cd_nao_vende_na_loja(banco):
    with pytest.raises(RegraDeNegocio, match="CD não faz venda física"):
        vendas.registrar_venda_fisica(SessaoVendas(banco), funcionario(), venda(id_unidade=CD))


def test_cpf_invalido_na_venda():
    with pytest.raises(ValidationError, match="CPF inválido"):
        venda(cpf_cliente="123")


# ---------- preparo e entrega ----------

def test_retirada_pronta_e_entregue_com_codigo(banco):
    pedido = pedido_pago(banco)

    vendas.marcar_pronto_para_retirada(SessaoVendas(banco), pedido.id_pedido)
    assert pedido.status == "pronto_para_retirada" and pedido.pronto_retirada_em is not None

    with pytest.raises(RegraDeNegocio, match="não confere"):
        vendas.entregar(SessaoVendas(banco), pedido.id_pedido, "CLERRADO")
    entregue = vendas.entregar(SessaoVendas(banco), pedido.id_pedido, pedido.codigo_venda.lower())
    assert entregue["status"] == "entregue" and entregue["entregue_em"] is not None


def test_entrega_em_casa_enviada_e_entregue(banco):
    pedido = pedido_pago(banco, "entrega")
    with pytest.raises(RegraDeNegocio, match="enviado, não retirado"):
        vendas.marcar_pronto_para_retirada(SessaoVendas(banco), pedido.id_pedido)

    vendas.enviar(SessaoVendas(banco), pedido.id_pedido)
    assert pedido.status == "enviado" and pedido.enviado_em is not None
    assert vendas.entregar(SessaoVendas(banco), pedido.id_pedido, None)["status"] == "entregue"


def test_nao_envia_pedido_sem_pagamento(banco):
    com_estoque(banco, CAMISA, CD, "online", 5)
    pedido = pedidos.checkout(SessaoVendas(banco), cliente(), entrega())
    with pytest.raises(RegraDeNegocio, match="aguardando pagamento"):
        vendas.enviar(SessaoVendas(banco), pedido["id_pedido"])


def test_retiradas_prontas_ha_mais_de_n_dias(monkeypatch):
    recebidos = {}
    monkeypatch.setattr(pedido_repository, "listar_pedidos",
                        lambda db, limit, offset, **f: (recebidos.update(f), ([], 0))[1])
    vendas.listar(SessaoFalsa(), 50, 0, pronto_ha_mais_de_dias=5)
    assert pedidos.agora() - recebidos["pronto_antes_de"] >= timedelta(days=5)


# ---------- cancelamento pela equipe ----------

def test_equipe_cancela_pago_com_estorno_e_retorno(banco):
    pedido = pedido_pago(banco)
    db = SessaoVendas(banco)

    cancelado = vendas.cancelar(db, funcionario(), pedido.id_pedido, "cliente desistiu no balcão")

    assert cancelado["status"] == "cancelado" and cancelado["motivo_cancelamento"] == "equipe"
    assert cancelado["id_cancelado_por"] == ID_FUNCIONARIO
    assert cancelado["justificativa_cancelamento"] == "cliente desistiu no balcão"
    assert [(p.tipo, p.valor) for p in cancelado["pagamentos"]] == [
        ("pagamento", Decimal("200.00")), ("estorno", Decimal("200.00"))]
    assert movimentacoes(db) == [("retorno_cancelamento", "online", 2)]
    assert saldo(banco, CAMISA, LOJA_SP) == (5, 0)


def test_equipe_cancela_aguardando_e_libera_reserva(banco):
    com_estoque(banco, CAMISA, LOJA_SP, "online", 5)
    pedido = pedidos.checkout(SessaoVendas(banco), cliente(), retirada())
    vendas.cancelar(SessaoVendas(banco), funcionario(), pedido["id_pedido"], "duplicado")
    assert saldo(banco, CAMISA, LOJA_SP) == (5, 0)


def test_enviado_nao_se_cancela(banco):
    pedido = pedido_pago(banco, "entrega")
    vendas.enviar(SessaoVendas(banco), pedido.id_pedido)
    with pytest.raises(RegraDeNegocio, match="cancelar um pedido enviado"):
        vendas.cancelar(SessaoVendas(banco), funcionario(), pedido.id_pedido, "motivo")


def test_rotas_de_vendas_exigem_permissao(api, banco):
    cliente_api = api(SessaoFalsa(), usuario=funcionario(), permitido=False)
    assert cliente_api.get("/vendas/pedidos").status_code == 403
    corpo = VendaFisica(**venda()).model_dump(mode="json")
    assert cliente_api.post("/vendas/pedidos", json=corpo).status_code == 403


# ---------- contas do caixa e ativação ----------

class AuthFalso:
    def __init__(self, erro=None):
        self.erro = erro
        self.chamadas = []
        self.id_novo = uuid.UUID("66666666-6666-6666-6666-666666666666")

    def gerar_link(self, tipo, email, redirecionar_para=None):
        self.chamadas.append(("gerar_link", tipo, email))
        if self.erro:
            raise self.erro
        return self.id_novo, f"https://teste/link/{tipo}"

    def __getattr__(self, nome):
        return lambda *args: self.chamadas.append((nome, *args))


@pytest.fixture
def contas(monkeypatch):
    estado = SimpleNamespace(usuarios={}, transferidos=[])
    por_cpf = lambda db, cpf: next((u for u in estado.usuarios.values() if u.cpf == cpf), None)  # noqa: E731
    m = monkeypatch.setattr
    m(usuario_repository, "cpf_em_uso", lambda db, cpf: por_cpf(db, cpf) is not None)
    m(usuario_repository, "email_em_uso", lambda db, e: any(u.email == e for u in estado.usuarios.values()))
    m(usuario_repository, "buscar_login", lambda db, e: None)
    m(usuario_repository, "buscar_cliente_por_cpf", por_cpf)
    m(permissao_repository, "buscar_usuario", lambda db, i: estado.usuarios.get(i))
    m(pedido_repository, "transferir_pedidos", lambda db, de, para: estado.transferidos.append((de, para)))
    return estado


def conta(estado, id_usuario, cpf, status="pendente_ativacao", email=None):
    usuario = Usuario(id_usuario=id_usuario, nome="Ana", email=email or f"{cpf}@x.com", cpf=cpf, tipo_conta="cliente",
                      status_conta=status)
    estado.usuarios[id_usuario] = usuario
    return usuario


def test_cadastro_no_caixa_devolve_link(contas):
    auth = AuthFalso()
    resposta = clientes.cadastrar_no_caixa(SessaoFalsa(), auth, {"nome": "Ana", "cpf": CPF, "email": "ana@x.com"})
    assert resposta["status_conta"] == "pendente_ativacao" and resposta["link_ativacao"] == "https://teste/link/invite"
    assert resposta["id_usuario"] == auth.id_novo


def test_cadastro_no_caixa_com_cpf_repetido(contas):
    conta(contas, ID_CLIENTE, CPF)
    with pytest.raises(Conflito, match="CPF"):
        clientes.cadastrar_no_caixa(SessaoFalsa(), AuthFalso(), {"nome": "Ana", "cpf": CPF, "email": "b@x.com"})


def test_falha_no_banco_apaga_o_login_do_caixa(contas):
    auth = AuthFalso()
    with pytest.raises(RuntimeError):
        clientes.cadastrar_no_caixa(SessaoFalsa(erro_commit=RuntimeError("x")), auth,
                                    {"nome": "Ana", "cpf": CPF, "email": "ana@x.com"})
    assert ("apagar_login", auth.id_novo) in auth.chamadas


def test_ativacao_confere_o_cpf(contas):
    usuario = conta(contas, ID_CLIENTE, CPF)
    auth = AuthFalso()
    with pytest.raises(RegraDeNegocio, match="não confere"):
        clientes.ativar(SessaoFalsa(), auth, str(ID_CLIENTE), CPF_CERTO, "segredo1")

    clientes.ativar(SessaoFalsa(), auth, str(ID_CLIENTE), CPF, "segredo1")

    assert usuario.status_conta == "ativa" and ("definir_senha", ID_CLIENTE, "segredo1") in auth.chamadas


def test_conta_ja_ativada(contas):
    conta(contas, ID_CLIENTE, CPF, status="ativa")
    with pytest.raises(RegraDeNegocio, match="já foi ativada"):
        clientes.ativar(SessaoFalsa(), AuthFalso(), str(ID_CLIENTE), CPF, "segredo1")


def test_novo_link_so_para_conta_pendente(contas):
    conta(contas, ID_CLIENTE, CPF)
    assert clientes.novo_link(SessaoFalsa(), AuthFalso(), ID_CLIENTE)["link_ativacao"].endswith("magiclink")
    contas.usuarios[ID_CLIENTE].status_conta = "ativa"
    with pytest.raises(RegraDeNegocio):
        clientes.novo_link(SessaoFalsa(), AuthFalso(), ID_CLIENTE)


def test_corrigir_cpf_que_ja_tem_conta_junta_os_pedidos(contas):
    outro = uuid.UUID("77777777-7777-7777-7777-777777777777")
    errada = conta(contas, ID_CLIENTE, CPF, status="ativa")
    conta(contas, outro, CPF_CERTO, status="ativa")
    auth = AuthFalso()

    resposta = clientes.corrigir(SessaoFalsa(), auth, ID_CLIENTE, {"cpf": CPF_CERTO})

    assert contas.transferidos == [(ID_CLIENTE, outro)] and resposta["id_conta_mantida"] == outro
    assert errada.status_conta == "inativa" and ("bloquear_login", ID_CLIENTE) in auth.chamadas


def test_corrigir_email_de_conta_pendente_gera_link_novo(contas):
    usuario = conta(contas, ID_CLIENTE, CPF)
    auth = AuthFalso()

    resposta = clientes.corrigir(SessaoFalsa(), auth, ID_CLIENTE, {"email": "certo@x.com"})

    assert usuario.email == "certo@x.com" and ("alterar_email", ID_CLIENTE, "certo@x.com") in auth.chamadas
    assert resposta["link_ativacao"] is not None


def test_corrigir_com_supabase_fora_do_ar(contas):
    conta(contas, ID_CLIENTE, CPF)
    auth = AuthFalso()
    auth.alterar_email = lambda *a: (_ for _ in ()).throw(ErroSupabase(503, None, "fora"))
    with pytest.raises(Exception, match="indisponível"):
        clientes.corrigir(SessaoFalsa(), auth, ID_CLIENTE, {"email": "certo@x.com"})


def test_busca_por_cpf_invalido(contas):
    with pytest.raises(RegraDeNegocio, match="CPF inválido"):
        clientes.buscar_por_cpf(SessaoFalsa(), "111")


def test_rota_de_ativacao_usa_so_o_token(api, contas, monkeypatch):
    from src.middlewares.auth import get_current_user
    conta(contas, ID_CLIENTE, CPF)
    app.dependency_overrides[get_current_user] = lambda: str(ID_CLIENTE)
    app.dependency_overrides[get_supabase_admin] = AuthFalso
    resposta = api(SessaoFalsa()).post("/ativacao", json={"cpf": CPF, "senha": "segredo1"})
    assert resposta.status_code == 200 and resposta.json()["status_conta"] == "ativa"
