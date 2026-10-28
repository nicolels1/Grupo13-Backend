from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.database.session import get_db
from src.dependencies.assistente import conversa_do_cabecalho, exige_chave_assistente
from src.entities.assistente import (
    BuscaPecasSaida, CodigoEntrada, CodigoEnviado, ConfirmacaoSaida, ConversaEntrada, CpfEntrada, EstadoConversa,
    EstoqueSaida, EtapaEntrada, LinkCarrinhoSaida, LojasDaMarca, MeusPedidosSaida, MeusPontosSaida, PedidosEntrada,
    ResumoDoN8nEntrada, VocabularioSaida,
)
from src.entities.comum import Lista, Marca
from src.use_cases import assistente, assistente_conta, resumo_conversa
from src.use_cases.horarios import BRASILIA

# ferramentas do assistente do cliente, chamadas pelo n8n com a chave no cabeçalho X-Assistente-Chave.
# Sem login de cliente, só o que a vitrine mostra a qualquer pessoa; pontos e pedidos só depois do código
# por e-mail, e sempre do cliente confirmado na conversa (canal + telefone ou sessão)
router = APIRouter(
    prefix="/assistente",
    tags=["assistente"],
    dependencies=[Depends(exige_chave_assistente)],
    responses={401: {"description": "Chave do assistente inválida"}, 503: {"description": "Assistente indisponível"}},
)


@router.get(
    "/pecas",
    response_model=BuscaPecasSaida,
    description="Até 5 peças das duas marcas para uma descrição do cliente (ex.: camisa de botão). resultado "
                "diz se achou o que foi pedido (exato) ou se mostra peças parecidas ou novidades.",
)
def buscar_pecas(
    descricao: str = Query(min_length=2, max_length=100, description="O que o cliente procura, em palavras do catálogo"),
    cor: str | None = Query(default=None, max_length=50),
    tamanho: str | None = Query(default=None, max_length=20, description="Só peças com esse tamanho à venda"),
    marca: Marca | None = Query(default=None, description="Só quando o cliente falar a marca"),
    categoria: str | None = Query(default=None, max_length=100),
    conversa: tuple = Depends(conversa_do_cabecalho),
    db: Session = Depends(get_db),
):
    return resumo_conversa.com_assunto(
        db, *conversa, "pecas", lambda: assistente.buscar_pecas(db, descricao, cor, tamanho, marca, categoria)
    )


@router.get(
    "/estoque",
    response_model=EstoqueSaida,
    responses={404: {"description": "Peça não encontrada ou sem essa cor e tamanho (a mensagem traz as opções)"}},
    description="Se a peça tem no site e em quais lojas, já sem o reservado. A quantidade só vem quando são "
                "até 3 peças (está acabando).",
)
def ver_estoque(
    id_produto: int,
    cor: str = Query(max_length=50),
    tamanho: str = Query(max_length=20),
    conversa: tuple = Depends(conversa_do_cabecalho),
    db: Session = Depends(get_db),
):
    return resumo_conversa.com_assunto(
        db, *conversa, "estoque", lambda: assistente.ver_estoque(db, id_produto, cor, tamanho)
    )


@router.get(
    "/link-carrinho",
    response_model=LinkCarrinhoSaida,
    responses={
        404: {"description": "Peça não encontrada"},
        422: {"description": "Sem estoque para o site (a mensagem diz em quais lojas tem)"},
    },
    description="Link que abre o site com a peça no carrinho. Só sai com estoque para o site.",
)
def link_carrinho(id_variante: int, conversa: tuple = Depends(conversa_do_cabecalho), db: Session = Depends(get_db)):
    return resumo_conversa.com_assunto(db, *conversa, "carrinho", lambda: assistente.link_carrinho(db, id_variante))


@router.get(
    "/lojas",
    response_model=Lista[LojasDaMarca],
    description="Lojas por marca, com endereço, o horário de hoje em texto pronto e o da semana.",
)
def lojas(conversa: tuple = Depends(conversa_do_cabecalho), db: Session = Depends(get_db)):
    return resumo_conversa.com_assunto(db, *conversa, "lojas", lambda: assistente.lojas(db, datetime.now(BRASILIA)))


@router.get(
    "/vocabulario",
    response_model=VocabularioSaida,
    description="Categorias e cores que existem no catálogo, para buscar peças com as palavras certas.",
)
def vocabulario(db: Session = Depends(get_db)):
    return assistente.vocabulario(db)


# ---------- conversa e confirmação por e-mail (fluxo fixo do n8n, sem IA) ----------

@router.post(
    "/conversa",
    response_model=EstadoConversa,
    description="Registra a mensagem do cliente: chamada no começo de toda mensagem. Diz a etapa, se a conta "
                "está confirmada, se a conversa é nova (30 min parada: apagar a memória curta) e o limite do dia.",
)
def registrar_mensagem(dados: ConversaEntrada, db: Session = Depends(get_db)):
    return assistente_conta.registrar_mensagem(db, dados.canal, dados.id_externo, datetime.now(BRASILIA))


@router.put(
    "/conversa/etapa",
    response_model=EstadoConversa,
    responses={404: {"description": "Conversa não registrada"}},
    description="Põe a conversa esperando o CPF (o cliente pediu pontos ou pedidos) ou a devolve para a IA.",
)
def mudar_etapa(dados: EtapaEntrada, db: Session = Depends(get_db)):
    return assistente_conta.mudar_etapa(db, dados.canal, dados.id_externo, dados.etapa, datetime.now(BRASILIA))


@router.patch(
    "/conversa/resumo",
    status_code=204,
    responses={404: {"description": "Conversa não registrada"}},
    description="Anota no resumo anônimo da conversa o 👍/👎 do cliente e se ele falou de algo fora do tema.",
)
def registrar_no_resumo(dados: ResumoDoN8nEntrada, db: Session = Depends(get_db)):
    resumo_conversa.registrar_do_n8n(db, dados.canal, dados.id_externo, dados.avaliacao, dados.fora_do_tema)


@router.post(
    "/verificacao/cpf",
    response_model=CodigoEnviado,
    responses={
        404: {"description": "Conversa não registrada"},
        422: {"description": "CPF inválido"},
        429: {"description": "Confirmação pausada ou 3 códigos no dia"},
    },
    description="Manda o código de 6 números ao e-mail do cadastro. A resposta é a mesma para CPF com e sem "
                "cadastro. O CPF não fica guardado.",
)
def pedir_codigo(dados: CpfEntrada, db: Session = Depends(get_db)):
    return assistente_conta.pedir_codigo(db, dados.canal, dados.id_externo, dados.cpf, datetime.now(BRASILIA))


@router.post(
    "/verificacao/codigo",
    response_model=ConfirmacaoSaida,
    responses={
        404: {"description": "Conversa não registrada"},
        422: {"description": "Código incorreto (diz quantas tentativas restam) ou expirado"},
        429: {"description": "5 erros: confirmação pausada por 30 min"},
    },
    description="Confere o código; certo, liga a conversa à conta até ela terminar (30 min sem mensagem).",
)
def conferir_codigo(dados: CodigoEntrada, db: Session = Depends(get_db)):
    return assistente_conta.conferir_codigo(db, dados.canal, dados.id_externo, dados.codigo, datetime.now(BRASILIA))


# ---------- ferramentas com login (a IA chama; o n8n injeta a conversa) ----------

@router.post(
    "/meus-pontos",
    response_model=MeusPontosSaida,
    responses={401: {"description": "Conta não confirmada nesta conversa: pedir o CPF"}},
    description="Pontos por marca: saldo, valor, categoria, vencimento e quanto falta para subir. Fica registrado "
                "o que foi entregue (auditoria).",
)
def meus_pontos(dados: ConversaEntrada, db: Session = Depends(get_db)):
    return resumo_conversa.com_assunto(
        db, dados.canal, dados.id_externo, "pontos",
        lambda: assistente_conta.meus_pontos(db, dados.canal, dados.id_externo, datetime.now(BRASILIA)),
    )


@router.post(
    "/meus-pedidos",
    response_model=MeusPedidosSaida,
    responses={
        401: {"description": "Conta não confirmada nesta conversa: pedir o CPF"},
        404: {"description": "Pedido não encontrado"},
    },
    description="Últimos 5 pedidos (ou um): status, onde e até quando retirar. Fica registrado o que foi "
                "entregue (auditoria).",
)
def meus_pedidos(dados: PedidosEntrada, db: Session = Depends(get_db)):
    return resumo_conversa.com_assunto(
        db, dados.canal, dados.id_externo, "pedidos",
        lambda: assistente_conta.meus_pedidos(db, dados.canal, dados.id_externo, dados.id_pedido,
                                              datetime.now(BRASILIA)),
    )
