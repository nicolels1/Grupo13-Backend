from datetime import timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from src.models.atendimento import Chamado
from src.models.contas import Usuario
from src.models.vendas import Pedido
from src.repositories import atendimento_repository, estoque_repository, unidade_repository
from src.repositories import pedido_repository as repo
from src.use_cases import pagamentos
from src.use_cases.erros import RecursoNaoEncontrado, RegraDeNegocio
from src.use_cases.estoque import conferir_disponivel
from src.use_cases.pedidos import agora, detalhar, quantidades

PRAZO = timedelta(days=30)  # troca e devolução: até 30 dias após a entrega (case, seção 5)


def _chamado_com_pedido(db: Session, id_chamado: int) -> tuple[Chamado, Pedido]:
    chamado = atendimento_repository.travar_chamado(db, id_chamado)
    if chamado is None:
        raise RecursoNaoEncontrado("Chamado não encontrado")
    if chamado.status == "concluido":
        raise RegraDeNegocio("Chamado concluído não muda mais: não há reabertura")
    if chamado.id_pedido is None:
        raise RegraDeNegocio("O chamado precisa apontar para o pedido")
    return chamado, repo.buscar_pedido(db, chamado.id_pedido, travar=True)


# troca e devolução: pedido entregue, dentro do prazo, recebidas em qualquer loja (não no CD)
def _conferir_troca_ou_devolucao(db: Session, chamado: Chamado, pedido: Pedido, id_unidade: int) -> None:
    if chamado.categoria != "troca_devolucao":
        raise RegraDeNegocio("Troca e devolução são feitas em chamado da categoria troca ou devolução")
    if pedido.status != "entregue":
        raise RegraDeNegocio("Só pedido entregue tem troca ou devolução")
    if pedido.entregue_em + PRAZO < agora():
        raise RegraDeNegocio("Passou o prazo de 30 dias após a entrega")
    loja = unidade_repository.buscar_unidade(db, id_unidade)
    if loja is None:
        raise RecursoNaoEncontrado("Loja não encontrada")
    if loja.tipo != "loja" or not loja.ativo:
        raise RegraDeNegocio("A peça volta numa loja ativa (o CD não atende público)")


# peças do pedido que o cliente ainda tem: compradas + recebidas em troca − já devolvidas
def em_maos(db: Session, pedido: Pedido) -> dict[int, int]:
    pecas = quantidades(repo.itens_do_pedido(db, pedido.id_pedido))
    for tipo, id_variante, soma in repo.trocas_e_devolucoes(db, pedido.id_pedido):
        # devolucao entra no estoque (+) e sai das mãos do cliente; saida_troca sai do estoque (−) e entra
        pecas[id_variante] = pecas.get(id_variante, 0) - soma
    return pecas


def _conferir_pecas(pecas: dict[int, int], itens: list[dict]) -> None:
    for item in itens:
        if item["quantidade"] > pecas.get(item["id_variante"], 0):
            raise RegraDeNegocio(f"O cliente não tem {item['quantidade']} peça(s) da variante {item['id_variante']} "
                                 "deste pedido para devolver")


def _devolver_na_loja(db: Session, usuario: Usuario, chamado: Chamado, id_unidade: int, itens: list[dict]) -> None:
    for item in sorted(itens, key=lambda i: i["id_variante"]):
        estoque_repository.inserir_movimentacao(
            db, id_variante=item["id_variante"], id_unidade=id_unidade, canal="loja_fisica", tipo="devolucao",
            quantidade=item["quantidade"], id_chamado=chamado.id_chamado, id_usuario=usuario.id_usuario,
        )


def _estornar(db: Session, pedido: Pedido, estornos: list[dict], id_chamado: int) -> None:
    lancamentos = repo.pagamentos_dos_pedidos(db, [pedido.id_pedido])[pedido.id_pedido]
    for pedido_estorno in estornos:
        original = next((p for p in lancamentos if p.id_pagamento == pedido_estorno["id_pagamento"]), None)
        if original is None:
            raise RecursoNaoEncontrado(f"Pagamento {pedido_estorno['id_pagamento']} não é deste pedido")
        pagamentos.estornar(db, pedido, original, pedido_estorno["valor"], lancamentos, id_chamado)


# devolução: a peça entra no estoque de loja física da loja que recebeu, com estorno ligado ao chamado;
# o pedido continua entregue e passa a indicar devolução parcial ou total (case, seção 5; ADR 0003)
def registrar_devolucao(db: Session, usuario: Usuario, id_chamado: int, dados: dict) -> dict:
    chamado, pedido = _chamado_com_pedido(db, id_chamado)
    _conferir_troca_ou_devolucao(db, chamado, pedido, dados["id_unidade"])
    pecas = em_maos(db, pedido)
    _conferir_pecas(pecas, dados["itens"])

    _devolver_na_loja(db, usuario, chamado, dados["id_unidade"], dados["itens"])
    _estornar(db, pedido, dados["estornos"], chamado.id_chamado)
    restantes = sum(pecas.values()) - sum(i["quantidade"] for i in dados["itens"])
    pedido.devolucao = "total" if restantes <= 0 else "parcial"
    db.commit()
    return detalhar(db, pedido.id_pedido)


# troca: a peça devolvida entra no estoque de loja física e a nova sai dele, na mesma loja.
# A nova é do mesmo produto (outra cor ou tamanho): o preço não muda e não há estorno
def registrar_troca(db: Session, usuario: Usuario, id_chamado: int, dados: dict) -> dict:
    chamado, pedido = _chamado_com_pedido(db, id_chamado)
    _conferir_troca_ou_devolucao(db, chamado, pedido, dados["id_unidade"])
    _conferir_pecas(em_maos(db, pedido), dados["itens"])

    variantes = repo.variantes_com_produto(
        db, [i["id_variante"] for i in dados["itens"]] + [i["id_variante_nova"] for i in dados["itens"]],
    )
    novas: dict[int, int] = {}
    for item in dados["itens"]:
        antiga, nova = variantes.get(item["id_variante"]), variantes.get(item["id_variante_nova"])
        if nova is None:
            raise RecursoNaoEncontrado(f"Variante {item['id_variante_nova']} não encontrada")
        if nova[0].id_produto != antiga[0].id_produto or not nova[0].ativo:
            raise RegraDeNegocio("A troca é por outra cor ou tamanho do mesmo produto, à venda")
        novas[item["id_variante_nova"]] = novas.get(item["id_variante_nova"], 0) + item["quantidade"]

    for id_variante in sorted(novas):
        linha = estoque_repository.travar_estoque(db, id_variante, dados["id_unidade"], ["loja_fisica"]).get("loja_fisica")
        conferir_disponivel(linha, novas[id_variante])

    _devolver_na_loja(db, usuario, chamado, dados["id_unidade"], dados["itens"])
    for id_variante in sorted(novas):
        estoque_repository.inserir_movimentacao(
            db, id_variante=id_variante, id_unidade=dados["id_unidade"], canal="loja_fisica", tipo="saida_troca",
            quantidade=-novas[id_variante], id_chamado=chamado.id_chamado, id_usuario=usuario.id_usuario,
        )
    db.commit()
    return detalhar(db, pedido.id_pedido)


# estorno pedido pelo atendimento sem troca nem devolução (ex.: problema na entrega); o atendente
# escolhe de qual pagamento sai, e o estorno fica ligado ao chamado (case, seção 5)
def registrar_estorno(db: Session, id_chamado: int, id_pagamento: int, valor: Decimal) -> dict:
    chamado, pedido = _chamado_com_pedido(db, id_chamado)
    if pedido.status in ("aguardando_pagamento", "cancelado"):
        raise RegraDeNegocio("Pedido sem pagamento a estornar pelo atendimento")
    _estornar(db, pedido, [{"id_pagamento": id_pagamento, "valor": valor}], chamado.id_chamado)
    db.commit()
    return detalhar(db, pedido.id_pedido)
