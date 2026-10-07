from dataclasses import dataclass
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
from src.use_cases.pedidos import agora, buscar, detalhar, pagina, quantidades
from src.utils.cpf import cpf_valido, normalizar_cpf

PRAZO = timedelta(days=30)  # troca e devolução: até 30 dias após a entrega (case, seção 5)


# quem registra e onde: pelo Atendimento, com o chamado; no balcão, a pessoa da equipe e a loja (ADR 0015)
@dataclass
class Registro:
    usuario: Usuario
    id_unidade: int
    id_chamado: int | None = None

    @property
    def origem(self) -> str:
        return "atendimento" if self.id_chamado is not None else "balcao"


# ---------- regras comuns ----------

# pedido entregue há no máximo 30 dias, com ou sem conta; a peça volta numa loja ativa (não no CD)
def _conferir_pedido_e_loja(db: Session, pedido: Pedido, id_unidade: int) -> None:
    if pedido.status != "entregue":
        raise RegraDeNegocio("Só pedido entregue tem troca ou devolução")
    if pedido.entregue_em + PRAZO < agora():
        raise RegraDeNegocio("Passou o prazo de 30 dias após a entrega")
    loja = unidade_repository.buscar_unidade(db, id_unidade)
    if loja is None:
        raise RecursoNaoEncontrado("Loja não encontrada")
    if loja.tipo != "loja" or not loja.ativo:
        raise RegraDeNegocio("Troca e devolução são feitas numa loja ativa (o CD não atende público)")


# peças do pedido que o cliente ainda tem: compradas + recebidas em troca − já devolvidas
def em_maos(db: Session, pedido: Pedido) -> dict[int, int]:
    pecas = quantidades(repo.itens_do_pedido(db, pedido.id_pedido))
    for _, id_variante, soma in repo.trocas_e_devolucoes(db, pedido.id_pedido):
        # devolucao entra no estoque (+) e sai das mãos do cliente; saida_troca sai do estoque (−) e entra
        pecas[id_variante] = pecas.get(id_variante, 0) - soma
    return pecas


def _conferir_pecas(pecas: dict[int, int], itens: list[dict]) -> None:
    for item in itens:
        if item["quantidade"] > pecas.get(item["id_variante"], 0):
            raise RegraDeNegocio(f"O cliente não tem {item['quantidade']} peça(s) da variante {item['id_variante']} "
                                 "deste pedido para devolver")


def _movimentar(db: Session, registro: Registro, pedido: Pedido, id_variante: int, tipo: str, quantidade: int) -> None:
    estoque_repository.inserir_movimentacao(
        db, id_variante=id_variante, id_unidade=registro.id_unidade, canal="loja_fisica", tipo=tipo,
        quantidade=quantidade, id_pedido=pedido.id_pedido, id_chamado=registro.id_chamado,
        id_usuario=registro.usuario.id_usuario,
    )


def _estornar(db: Session, pedido: Pedido, estornos: list[dict], registro: Registro) -> None:
    lancamentos = repo.pagamentos_dos_pedidos(db, [pedido.id_pedido])[pedido.id_pedido]
    for pedido_estorno in estornos:
        original = next((p for p in lancamentos if p.id_pagamento == pedido_estorno["id_pagamento"]), None)
        if original is None:
            raise RecursoNaoEncontrado(f"Pagamento {pedido_estorno['id_pagamento']} não é deste pedido")
        pagamentos.estornar(
            db, pedido, original, pedido_estorno["valor"], lancamentos, registro.origem,
            id_chamado=registro.id_chamado, id_registrado_por=registro.usuario.id_usuario,
            id_unidade=registro.id_unidade,
        )


# devolução: a peça entra no estoque de loja física da loja que recebeu, com estorno pelo mesmo meio de
# pagamento; o pedido continua entregue e passa a indicar devolução parcial ou total (case, seção 5)
def _devolver(db: Session, pedido: Pedido, registro: Registro, dados: dict) -> dict:
    _conferir_pedido_e_loja(db, pedido, registro.id_unidade)
    pecas = em_maos(db, pedido)
    _conferir_pecas(pecas, dados["itens"])

    for item in sorted(dados["itens"], key=lambda i: i["id_variante"]):
        _movimentar(db, registro, pedido, item["id_variante"], "devolucao", item["quantidade"])
    _estornar(db, pedido, dados["estornos"], registro)
    restantes = sum(pecas.values()) - sum(i["quantidade"] for i in dados["itens"])
    pedido.devolucao = "total" if restantes <= 0 else "parcial"
    db.commit()
    return detalhar(db, pedido.id_pedido)


# troca: a peça devolvida entra no estoque de loja física e a nova sai dele, na mesma loja.
# A nova é do mesmo produto (outra cor ou tamanho): o preço não muda e não há estorno
def _trocar(db: Session, pedido: Pedido, registro: Registro, dados: dict) -> dict:
    _conferir_pedido_e_loja(db, pedido, registro.id_unidade)
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
        linha = estoque_repository.travar_estoque(
            db, id_variante, registro.id_unidade, ["loja_fisica"]).get("loja_fisica")
        conferir_disponivel(linha, novas[id_variante])

    for item in sorted(dados["itens"], key=lambda i: i["id_variante"]):
        _movimentar(db, registro, pedido, item["id_variante"], "devolucao", item["quantidade"])
    for id_variante in sorted(novas):
        _movimentar(db, registro, pedido, id_variante, "saida_troca", -novas[id_variante])
    db.commit()
    return detalhar(db, pedido.id_pedido)


# ---------- pelo Atendimento (atender_chamado): com chamado ----------

def _chamado_com_pedido(db: Session, id_chamado: int) -> tuple[Chamado, Pedido]:
    chamado = atendimento_repository.travar_chamado(db, id_chamado)
    if chamado is None:
        raise RecursoNaoEncontrado("Chamado não encontrado")
    if chamado.status == "concluido":
        raise RegraDeNegocio("Chamado concluído não muda mais: não há reabertura")
    if chamado.id_pedido is None:
        raise RegraDeNegocio("O chamado precisa apontar para o pedido")
    return chamado, repo.buscar_pedido(db, chamado.id_pedido, travar=True)


def _registro_do_chamado(chamado: Chamado, usuario: Usuario, id_unidade: int) -> Registro:
    if chamado.categoria != "troca_devolucao":
        raise RegraDeNegocio("Troca e devolução são feitas em chamado da categoria troca ou devolução")
    return Registro(usuario, id_unidade, chamado.id_chamado)


def registrar_devolucao(db: Session, usuario: Usuario, id_chamado: int, dados: dict) -> dict:
    chamado, pedido = _chamado_com_pedido(db, id_chamado)
    return _devolver(db, pedido, _registro_do_chamado(chamado, usuario, dados["id_unidade"]), dados)


def registrar_troca(db: Session, usuario: Usuario, id_chamado: int, dados: dict) -> dict:
    chamado, pedido = _chamado_com_pedido(db, id_chamado)
    return _trocar(db, pedido, _registro_do_chamado(chamado, usuario, dados["id_unidade"]), dados)


# estorno pedido pelo atendimento sem troca nem devolução (ex.: problema na entrega); o atendente
# escolhe de qual pagamento sai, e o estorno fica ligado ao chamado (case, seção 5)
def registrar_estorno(db: Session, usuario: Usuario, id_chamado: int, id_pagamento: int, valor: Decimal) -> dict:
    chamado, pedido = _chamado_com_pedido(db, id_chamado)
    if pedido.status in ("aguardando_pagamento", "cancelado"):
        raise RegraDeNegocio("Pedido sem pagamento a estornar pelo atendimento")
    lancamentos = repo.pagamentos_dos_pedidos(db, [pedido.id_pedido])[pedido.id_pedido]
    original = next((p for p in lancamentos if p.id_pagamento == id_pagamento), None)
    if original is None:
        raise RecursoNaoEncontrado(f"Pagamento {id_pagamento} não é deste pedido")
    pagamentos.estornar(db, pedido, original, valor, lancamentos, "atendimento", id_chamado=chamado.id_chamado,
                        id_registrado_por=usuario.id_usuario)
    db.commit()
    return detalhar(db, pedido.id_pedido)


# ---------- no balcão (registrar_troca_devolucao): sem chamado ----------

# o pedido é achado pelo código da venda (notinha), pelo número do pedido ou pelo CPF; pelo CPF,
# vêm os pedidos entregues nos últimos 30 dias, pelo CPF da conta ou pelo CPF na nota
def buscar_no_balcao(db: Session, codigo_venda: str | None, id_pedido: int | None, cpf: str | None) -> list:
    if sum(valor is not None for valor in (codigo_venda, id_pedido, cpf)) != 1:
        raise RegraDeNegocio("Informe um só: código da venda, número do pedido ou CPF")
    if cpf is not None:
        cpf = normalizar_cpf(cpf)
        if not cpf_valido(cpf):
            raise RegraDeNegocio("CPF inválido")
        return pagina(db, 50, 0, cpf=cpf, status="entregue", entregue_desde=agora() - PRAZO)["items"]
    if codigo_venda is not None:
        pedido = repo.pedido_por_codigo(db, codigo_venda.strip())
        if pedido is None:
            raise RecursoNaoEncontrado("Pedido não encontrado")
        id_pedido = pedido.id_pedido
    return [detalhar(db, id_pedido)]


def devolver_no_balcao(db: Session, usuario: Usuario, id_pedido: int, dados: dict) -> dict:
    return _devolver(db, buscar(db, id_pedido, travar=True), Registro(usuario, dados["id_unidade"]), dados)


def trocar_no_balcao(db: Session, usuario: Usuario, id_pedido: int, dados: dict) -> dict:
    return _trocar(db, buscar(db, id_pedido, travar=True), Registro(usuario, dados["id_unidade"]), dados)
