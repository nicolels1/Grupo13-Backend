import secrets
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from src.models.contas import Usuario
from src.models.vendas import EnderecoEntrega, ItemPedido, Pagamento, Pedido
from src.repositories import catalogo_repository, estoque_repository, unidade_repository
from src.repositories import pedido_repository as repo
from src.use_cases import enderecos, pagamentos
from src.use_cases.catalogo import foto_principal
from src.use_cases.erros import Conflito, RecursoNaoEncontrado, RegraDeNegocio
from src.use_cases.estoque import conferir_disponivel

# frete da entrega em casa: valor fixo, grátis a partir de um valor de itens; zero na retirada e na loja
FRETE_ENTREGA = Decimal("19.90")
FRETE_GRATIS_A_PARTIR_DE = Decimal("299.00")
RESERVA = timedelta(minutes=15)  # ADR 0002
ALFABETO_DO_CODIGO = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sem 0/O e 1/I, que se confundem no comprovante
NAO_ENCONTRADO = "Pedido não encontrado"


def agora() -> datetime:
    return datetime.now(timezone.utc)


def novo_codigo_venda() -> str:
    return "CL" + "".join(secrets.choice(ALFABETO_DO_CODIGO) for _ in range(8))


def calcular_frete(valor_itens: Decimal, modalidade: str | None) -> Decimal:
    if modalidade != "entrega" or valor_itens >= FRETE_GRATIS_A_PARTIR_DE:
        return Decimal("0.00")
    return FRETE_ENTREGA


# ---------- montagem da resposta ----------

def montar(db: Session, linhas: list) -> list[dict]:
    """linhas: (Pedido, nome do cliente, nome da unidade), como devolve o repositório."""
    ids = [pedido.id_pedido for pedido, _, _ in linhas]
    itens = repo.itens_dos_pedidos(db, ids)
    lancamentos = repo.pagamentos_dos_pedidos(db, ids)
    enderecos_entrega = repo.enderecos_dos_pedidos(db, ids)
    # cada peça leva a foto da cor comprada, como nas mais vendidas do resumo
    imagens = catalogo_repository.imagens_dos_produtos(
        db, list({item["id_produto"] for lista in itens.values() for item in lista}))
    resultado = []
    for pedido, cliente, unidade in linhas:
        dados = {coluna.key: getattr(pedido, coluna.key) for coluna in Pedido.__table__.columns}
        dados.update(
            cliente=cliente, unidade=unidade,
            itens=[{**item, "foto_url": foto_principal(imagens[item["id_produto"]], item["cor"])}
                   for item in itens[pedido.id_pedido]],
            pagamentos=lancamentos[pedido.id_pedido], endereco_entrega=enderecos_entrega.get(pedido.id_pedido),
            valor_pago=pagamentos.valor_pago(lancamentos[pedido.id_pedido]),
            valor_itens=pedido.valor_total - pedido.valor_frete,
        )
        resultado.append(dados)
    return resultado


def detalhar(db: Session, id_pedido: int) -> dict:
    linha = repo.pedido_com_nomes(db, id_pedido)
    if linha is None:
        raise RecursoNaoEncontrado(NAO_ENCONTRADO)
    return montar(db, [linha])[0]


def pagina(db: Session, limit: int, offset: int, **filtros) -> dict:
    linhas, total = repo.listar_pedidos(db, limit, offset, **filtros)
    return {"items": montar(db, linhas), "total": total, "limit": limit, "offset": offset}


def buscar(db: Session, id_pedido: int, travar: bool = False) -> Pedido:
    pedido = repo.buscar_pedido(db, id_pedido, travar)
    if pedido is None:
        raise RecursoNaoEncontrado(NAO_ENCONTRADO)
    return pedido


# ---------- itens e estoque ----------

# cada variante pedida precisa existir e estar à venda (variante e produto ativos);
# devolve os itens com o preço do momento
def itens_a_venda(db: Session, itens: list[dict]) -> list[dict]:
    encontrados = repo.variantes_com_produto(db, [i["id_variante"] for i in itens])
    resultado = []
    for item in itens:
        if item["id_variante"] not in encontrados:
            raise RecursoNaoEncontrado(f"Variante {item['id_variante']} não encontrada")
        variante, produto = encontrados[item["id_variante"]]
        if not variante.ativo or not produto.ativo:
            raise RegraDeNegocio(f"{produto.nome} ({variante.cor} / {variante.tamanho}) não está à venda")
        resultado.append({
            "id_variante": variante.id_variante, "sku": variante.sku, "produto": produto.nome, "cor": variante.cor,
            "tamanho": variante.tamanho, "quantidade": item["quantidade"], "preco_unitario": variante.preco,
            "subtotal": variante.preco * item["quantidade"],
        })
    return resultado


def quantidades(itens) -> dict[int, int]:
    total: dict[int, int] = defaultdict(int)
    for item in itens:
        id_variante = item["id_variante"] if isinstance(item, dict) else item.id_variante
        total[id_variante] += item["quantidade"] if isinstance(item, dict) else item.quantidade
    return dict(total)


def _comparavel(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return sem_acento.strip().casefold()


# unidades ativas com todos os itens disponíveis no estoque online, com a soma do disponível
def unidades_com_tudo(db: Session, pedidas: dict[int, int]) -> list[dict]:
    por_unidade: dict[int, dict] = {}
    for linha in repo.disponivel_online(db, list(pedidas)):
        unidade = por_unidade.setdefault(linha["id_unidade"], {**linha, "cobertas": 0, "total": 0})
        if linha["disponivel"] >= pedidas[linha["id_variante"]]:
            unidade["cobertas"] += 1
        unidade["total"] += linha["disponivel"]
    return [u for u in por_unidade.values() if u["cobertas"] == len(pedidas)]


# entrega em casa: um CD com tudo; senão, uma loja que despacha, priorizando a mesma cidade, depois o
# mesmo estado, e desempatando pelo maior estoque (case, seção 5)
def escolher_unidade_entrega(candidatas: list[dict], cidade: str, uf: str) -> dict | None:
    cds = [u for u in candidatas if u["tipo"] == "cd"]
    grupo = cds or [u for u in candidatas if u["tipo"] == "loja" and u["despacha_online"]]
    if not grupo:
        return None
    return max(grupo, key=lambda u: (
        _comparavel(u["cidade"]) == _comparavel(cidade) and u["uf"] == uf, u["uf"] == uf, u["total"], -u["id_unidade"],
    ))


def lojas_para_retirada(candidatas: list[dict]) -> list[dict]:
    return sorted((u for u in candidatas if u["tipo"] == "loja"), key=lambda u: (u["uf"], u["cidade"], u["nome"]))


# trava as linhas de ESTOQUE sempre na mesma ordem (por variante), como o resto do sistema
def _travar(db: Session, id_unidade: int, canal: str, pedidas: dict[int, int]):
    for id_variante in sorted(pedidas):
        yield id_variante, estoque_repository.travar_estoque(db, id_variante, id_unidade, [canal]).get(canal)


def reservar(db: Session, id_unidade: int, pedidas: dict[int, int]) -> None:
    for id_variante, linha in _travar(db, id_unidade, "online", pedidas):
        conferir_disponivel(linha, pedidas[id_variante])
        linha.quantidade_reservada += pedidas[id_variante]


def liberar_reserva(db: Session, id_unidade: int, pedidas: dict[int, int]) -> None:
    for id_variante, linha in _travar(db, id_unidade, "online", pedidas):
        if linha is not None:
            linha.quantidade_reservada = max(0, linha.quantidade_reservada - pedidas[id_variante])


# pagamento aprovado: a reserva vira venda. A reserva sai antes da movimentação, para o saldo
# nunca ficar menor que o reservado no meio do caminho
def baixar_venda_online(db: Session, pedido: Pedido, pedidas: dict[int, int]) -> None:
    liberar_reserva(db, pedido.id_unidade, pedidas)
    db.flush()
    for id_variante in sorted(pedidas):
        estoque_repository.inserir_movimentacao(
            db, id_variante=id_variante, id_unidade=pedido.id_unidade, canal="online", tipo="venda",
            quantidade=-pedidas[id_variante], id_pedido=pedido.id_pedido,
        )


# cancelamento depois do pagamento: as peças voltam ao estoque online da unidade
def devolver_ao_estoque(db: Session, pedido: Pedido, pedidas: dict[int, int], id_usuario=None) -> None:
    for id_variante in sorted(pedidas):
        estoque_repository.inserir_movimentacao(
            db, id_variante=id_variante, id_unidade=pedido.id_unidade, canal="online", tipo="retorno_cancelamento",
            quantidade=pedidas[id_variante], id_pedido=pedido.id_pedido, id_usuario=id_usuario,
        )


def marcar_cancelado(pedido: Pedido, motivo: str, id_cancelado_por=None, justificativa: str | None = None) -> None:
    pedido.status = "cancelado"
    pedido.motivo_cancelamento = motivo
    pedido.id_cancelado_por = id_cancelado_por
    pedido.justificativa_cancelamento = justificativa
    pedido.cancelado_em = agora()


# pedido ainda sem pagamento: libera a reserva e a cobrança pendente expira junto
def cancelar_aguardando(db: Session, pedido: Pedido, lancamentos: list[Pagamento], motivo: str, **autor) -> None:
    liberar_reserva(db, pedido.id_unidade, quantidades(repo.itens_do_pedido(db, pedido.id_pedido)))
    pagamentos.recusar_pendentes(lancamentos)
    marcar_cancelado(pedido, motivo, **autor)


def reserva_vencida(pedido: Pedido) -> bool:
    return pedido.status == "aguardando_pagamento" and pedido.reserva_expira_em is not None \
        and pedido.reserva_expira_em <= agora()


# ---------- plataforma do cliente ----------

def resumo_carrinho(db: Session, itens: list[dict]) -> dict:
    resumo = itens_a_venda(db, itens)
    valor_itens = sum((i["subtotal"] for i in resumo), Decimal("0.00"))
    candidatas = unidades_com_tudo(db, quantidades(itens))
    frete = calcular_frete(valor_itens, "entrega")
    return {
        "itens": resumo, "valor_itens": valor_itens, "frete_entrega": frete,
        "frete_gratis_a_partir_de": FRETE_GRATIS_A_PARTIR_DE,
        "total_entrega": valor_itens + frete, "total_retirada": valor_itens,
        "entrega_disponivel": escolher_unidade_entrega(candidatas, "", "") is not None,
        "lojas_retirada": lojas_para_retirada(candidatas),
    }


# checkout: escolhe a unidade, reserva as peças no estoque online por 15 minutos e cria o pedido
# aguardando pagamento; o estoque só baixa quando o pagamento é aprovado (ADR 0002)
def checkout(db: Session, cliente: Usuario, dados: dict) -> dict:
    resumo = itens_a_venda(db, dados["itens"])
    pedidas = quantidades(dados["itens"])
    candidatas = unidades_com_tudo(db, pedidas)

    endereco = None
    if dados["modalidade"] == "entrega":
        endereco = enderecos.do_cliente(db, cliente, dados["id_endereco"])
        unidade = escolher_unidade_entrega(candidatas, endereco.cidade, endereco.uf)
        if unidade is None:
            raise RegraDeNegocio("Nenhum CD ou loja tem todos os itens para entrega agora")
    else:
        loja = unidade_repository.buscar_unidade(db, dados["id_unidade_retirada"])
        if loja is None:
            raise RecursoNaoEncontrado("Loja não encontrada")
        if loja.tipo != "loja":
            raise RegraDeNegocio("Retirada só em loja (o CD não atende público)")
        unidade = next((u for u in candidatas if u["id_unidade"] == loja.id_unidade), None)
        if unidade is None:
            raise RegraDeNegocio("Essa loja não tem todos os itens para retirada agora")

    reservar(db, unidade["id_unidade"], pedidas)
    valor_itens = sum((i["subtotal"] for i in resumo), Decimal("0.00"))
    frete = calcular_frete(valor_itens, dados["modalidade"])
    pedido = Pedido(
        codigo_venda=novo_codigo_venda(), id_cliente=cliente.id_usuario, id_unidade=unidade["id_unidade"],
        canal="online", modalidade=dados["modalidade"], status="aguardando_pagamento", valor_frete=frete,
        valor_total=valor_itens + frete, reserva_expira_em=agora() + RESERVA,
    )
    db.add(pedido)
    db.flush()
    db.add_all(ItemPedido(id_pedido=pedido.id_pedido, id_variante=i["id_variante"], quantidade=i["quantidade"],
                          preco_unitario=i["preco_unitario"]) for i in resumo)
    if endereco is not None:
        db.add(EnderecoEntrega(id_pedido=pedido.id_pedido, **{
            campo: getattr(endereco, campo) for campo in ("rua", "numero", "complemento", "bairro", "cidade", "uf", "cep")
        }))
    db.commit()
    return detalhar(db, pedido.id_pedido)


# pedido de outra pessoa responde como inexistente: trocar o número na URL não revela nada
def do_cliente(db: Session, cliente: Usuario, id_pedido: int, travar: bool = False) -> Pedido:
    pedido = repo.buscar_pedido(db, id_pedido, travar)
    if pedido is None or pedido.id_cliente != cliente.id_usuario:
        raise RecursoNaoEncontrado(NAO_ENCONTRADO)
    return pedido


def listar_do_cliente(db: Session, cliente: Usuario, limit: int, offset: int, status: str | None = None) -> dict:
    return pagina(db, limit, offset, id_cliente=cliente.id_usuario, status=status)


def detalhar_do_cliente(db: Session, cliente: Usuario, id_pedido: int) -> dict:
    do_cliente(db, cliente, id_pedido)
    return detalhar(db, id_pedido)


# cobrança do valor que falta, no gateway simulado; trocar de método recusa a cobrança anterior
def criar_cobranca(db: Session, cliente: Usuario, id_pedido: int, metodo: str) -> dict:
    pedido = do_cliente(db, cliente, id_pedido, travar=True)
    if pedido.status != "aguardando_pagamento":
        raise RegraDeNegocio("Este pedido não está aguardando pagamento")
    if reserva_vencida(pedido):
        raise RegraDeNegocio("A reserva de 15 minutos venceu: o pedido será cancelado. Faça um novo pedido")
    lancamentos = repo.pagamentos_dos_pedidos(db, [id_pedido])[id_pedido]
    pagamentos.recusar_pendentes(lancamentos)
    falta = pedido.valor_total - pagamentos.valor_pago(lancamentos)
    db.add(Pagamento(
        id_pedido=id_pedido, tipo="pagamento", metodo=metodo, id_transacao_gateway=pagamentos.id_transacao(metodo),
        valor=falta, status="pendente",
    ))
    db.commit()
    return detalhar(db, id_pedido)


# resposta do gateway simulado. Aprovado e cobrindo o total: o pedido fica pago e o estoque baixa.
# Pagamento que chega depois do cancelamento (reserva vencida) é estornado na hora (ADR 0002)
def responder_cobranca(db: Session, cliente: Usuario, id_pagamento: int, aprovado: bool) -> dict:
    encontrado = repo.buscar_pagamento(db, id_pagamento)
    if encontrado is None:
        raise RecursoNaoEncontrado("Cobrança não encontrada")
    pedido = do_cliente(db, cliente, encontrado.id_pedido, travar=True)
    lancamentos = repo.pagamentos_dos_pedidos(db, [pedido.id_pedido])[pedido.id_pedido]
    pagamento = next(p for p in lancamentos if p.id_pagamento == id_pagamento)
    if pagamento.tipo != "pagamento" or pagamento.status != "pendente":
        raise Conflito("Esta cobrança já foi respondida")

    if not aprovado:
        pagamento.status = "recusado"
        db.commit()
        return detalhar(db, pedido.id_pedido)

    pagamento.status = "aprovado"
    if reserva_vencida(pedido):
        cancelar_aguardando(db, pedido, lancamentos, "reserva_vencida")
    if pedido.status == "cancelado":
        pagamentos.estornar(db, pedido, pagamento, pagamento.valor, lancamentos, "cancelamento")
    elif pagamentos.valor_pago(lancamentos) >= pedido.valor_total:
        baixar_venda_online(db, pedido, quantidades(repo.itens_do_pedido(db, pedido.id_pedido)))
        pedido.status = "pago"
        pedido.pago_em = agora()
    db.commit()
    return detalhar(db, pedido.id_pedido)


# o cliente cancela até o pedido estar pago; depois disso, pelo atendimento (case, seção 5)
def cancelar_pelo_cliente(db: Session, cliente: Usuario, id_pedido: int) -> dict:
    pedido = do_cliente(db, cliente, id_pedido, travar=True)
    if pedido.status != "aguardando_pagamento":
        raise RegraDeNegocio("Só dá para cancelar antes do pagamento; depois, abra um chamado")
    lancamentos = repo.pagamentos_dos_pedidos(db, [id_pedido])[id_pedido]
    cancelar_aguardando(db, pedido, lancamentos, "cliente", id_cancelado_por=cliente.id_usuario)
    db.commit()
    return detalhar(db, id_pedido)


# compra feita na loja sem CPF: o cliente liga à própria conta pelo código do comprovante, uma única vez
def reivindicar(db: Session, cliente: Usuario, codigo_venda: str) -> dict:
    pedido = repo.pedido_por_codigo(db, codigo_venda)
    if pedido is None or pedido.canal != "loja_fisica":
        raise RecursoNaoEncontrado("Nenhuma compra na loja com esse código")
    if pedido.id_cliente is not None:
        raise Conflito("Essa compra já está ligada a uma conta")
    pedido.id_cliente = cliente.id_usuario
    db.commit()
    return detalhar(db, pedido.id_pedido)
