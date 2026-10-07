from datetime import timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from src.models.contas import Usuario
from src.models.vendas import ItemPedido, Pagamento, Pedido
from src.repositories import estoque_repository, unidade_repository, usuario_repository
from src.repositories import pedido_repository as repo
from src.use_cases import pagamentos
from src.use_cases.erros import RecursoNaoEncontrado, RegraDeNegocio
from src.use_cases.estoque import conferir_disponivel, interpretar_momento
from src.use_cases.pedidos import (
    agora, buscar, cancelar_aguardando, detalhar, devolver_ao_estoque, itens_a_venda, marcar_cancelado,
    novo_codigo_venda, pagina, quantidades,
)


def _exigir(pedido: Pedido, status: tuple[str, ...], acao: str) -> None:
    if pedido.status not in status:
        raise RegraDeNegocio(f"Não dá para {acao} um pedido {pedido.status.replace('_', ' ')}")


# filtros da Visão Geral (pedidos pagos para preparar, retiradas prontas há mais de N dias) e do Caixa
# (vendas de hoje): data sem hora vale o dia inteiro, no horário de Brasília
def listar(db: Session, limit: int, offset: int, pronto_ha_mais_de_dias: int | None = None,
           de: str | None = None, ate: str | None = None, **filtros) -> dict:
    if de:
        filtros["criado_desde"] = interpretar_momento(de, fim_do_dia=False)
    if ate:
        filtros["criado_ate"] = interpretar_momento(ate)
    if pronto_ha_mais_de_dias is not None:
        filtros["pronto_antes_de"] = agora() - timedelta(days=pronto_ha_mais_de_dias)
    return pagina(db, limit, offset, **filtros)


# venda na loja: registrada por quem vende, nasce paga e entregue, e baixa o estoque de loja física
# no ato (case, seção 5). A soma dos pagamentos precisa fechar com o total (sem frete)
def registrar_venda_fisica(db: Session, vendedor: Usuario, dados: dict) -> dict:
    unidade = unidade_repository.buscar_unidade(db, dados["id_unidade"])
    if unidade is None:
        raise RecursoNaoEncontrado("Unidade não encontrada")
    if not unidade.ativo:
        raise RegraDeNegocio("Unidade desativada")
    if unidade.tipo != "loja":
        raise RegraDeNegocio("O CD não faz venda física")

    # o caixa não cria conta (ADR 0014): CPF com conta liga o pedido a ela; sem conta, o CPF fica
    # guardado e o pedido passa para a conta quando ela for criada pelo site
    id_cliente, cpf_nota = None, dados.get("cpf_nota")
    if cpf_nota:
        cliente = usuario_repository.buscar_cliente_por_cpf(db, cpf_nota)
        if cliente is not None:
            id_cliente = cliente.id_usuario

    resumo = itens_a_venda(db, dados["itens"])
    total = sum((i["subtotal"] for i in resumo), Decimal("0.00"))
    pago = sum((p["valor"] for p in dados["pagamentos"]), Decimal("0.00"))
    if pago != total:
        raise RegraDeNegocio(f"Os pagamentos somam R$ {pago}, mas o total é R$ {total}")

    pedidas = quantidades(dados["itens"])
    for id_variante in sorted(pedidas):
        linha = estoque_repository.travar_estoque(db, id_variante, unidade.id_unidade, ["loja_fisica"]).get("loja_fisica")
        conferir_disponivel(linha, pedidas[id_variante])

    momento = agora()
    pedido = Pedido(
        codigo_venda=novo_codigo_venda(), id_cliente=id_cliente, id_unidade=unidade.id_unidade,
        id_registrado_por=vendedor.id_usuario, cpf_nota=cpf_nota, canal="loja_fisica", status="entregue",
        valor_frete=Decimal("0.00"), valor_total=total, pago_em=momento, entregue_em=momento,
    )
    db.add(pedido)
    db.flush()
    db.add_all(ItemPedido(id_pedido=pedido.id_pedido, id_variante=i["id_variante"], quantidade=i["quantidade"],
                          preco_unitario=i["preco_unitario"]) for i in resumo)
    db.add_all(Pagamento(
        id_pedido=pedido.id_pedido, tipo="pagamento", metodo=p["metodo"],
        id_transacao_gateway=pagamentos.id_transacao(p["metodo"]), valor=p["valor"], status="aprovado",
    ) for p in dados["pagamentos"])
    for id_variante in sorted(pedidas):
        estoque_repository.inserir_movimentacao(
            db, id_variante=id_variante, id_unidade=unidade.id_unidade, canal="loja_fisica", tipo="venda",
            quantidade=-pedidas[id_variante], id_pedido=pedido.id_pedido, id_usuario=vendedor.id_usuario,
        )
    db.commit()
    return detalhar(db, pedido.id_pedido)


# ---------- preparo e entrega (preparar_entregar_pedido) ----------

def enviar(db: Session, id_pedido: int) -> dict:
    pedido = buscar(db, id_pedido, travar=True)
    _exigir(pedido, ("pago",), "enviar")
    if pedido.modalidade != "entrega":
        raise RegraDeNegocio("Pedido de retirada não é enviado: marque como pronto para retirada")
    pedido.status, pedido.enviado_em = "enviado", agora()
    db.commit()
    return detalhar(db, id_pedido)


# a partir daqui o cliente tem 7 dias para retirar; depois, o banco cancela com estorno
def marcar_pronto_para_retirada(db: Session, id_pedido: int) -> dict:
    pedido = buscar(db, id_pedido, travar=True)
    _exigir(pedido, ("pago",), "marcar como pronto")
    if pedido.modalidade != "retirada":
        raise RegraDeNegocio("Pedido de entrega em casa é enviado, não retirado")
    pedido.status, pedido.pronto_retirada_em = "pronto_para_retirada", agora()
    db.commit()
    return detalhar(db, id_pedido)


# na retirada, quem entrega confere o código do pedido (e o documento) mostrado pelo cliente
def entregar(db: Session, id_pedido: int, codigo_venda: str | None) -> dict:
    pedido = buscar(db, id_pedido, travar=True)
    _exigir(pedido, ("enviado", "pronto_para_retirada"), "entregar")
    if pedido.status == "pronto_para_retirada" and (codigo_venda or "").strip().upper() != pedido.codigo_venda:
        raise RegraDeNegocio("Código do pedido não confere")
    pedido.status, pedido.entregue_em = "entregue", agora()
    db.commit()
    return detalhar(db, id_pedido)


# ---------- cancelamento pela equipe (cancelar_pedido_equipe) ----------

# antes do envio: sem pagamento, libera a reserva; já pago, estorna tudo e as peças voltam ao estoque
# online. Enviado ou entregue não se cancela: vira troca ou devolução pelo atendimento
def cancelar(db: Session, usuario: Usuario, id_pedido: int, justificativa: str) -> dict:
    pedido = buscar(db, id_pedido, travar=True)
    _exigir(pedido, ("aguardando_pagamento", "pago", "pronto_para_retirada"), "cancelar")
    lancamentos = repo.pagamentos_dos_pedidos(db, [id_pedido])[id_pedido]
    autor = {"id_cancelado_por": usuario.id_usuario, "justificativa": justificativa}
    if pedido.status == "aguardando_pagamento":
        cancelar_aguardando(db, pedido, lancamentos, "equipe", **autor)
    else:
        pagamentos.estornar_tudo(db, pedido, lancamentos)
        devolver_ao_estoque(db, pedido, quantidades(repo.itens_do_pedido(db, id_pedido)), usuario.id_usuario)
        marcar_cancelado(pedido, "equipe", **autor)
    db.commit()
    return detalhar(db, id_pedido)
