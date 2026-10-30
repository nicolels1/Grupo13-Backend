from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session

from src.database.session import get_db
from src.dependencies.assistente import conversa_do_cabecalho, exige_chave_assistente
from src.dependencies.supabase import get_storage
from src.entities.assistente import (
    AvisoPontosSaida, BuscaPecasSaida, ChamadoAbertoSaida, ChamadoDoAssistenteEntrada, CodigoEntrada, CodigoEnviado, ConfirmacaoSaida,
    ConversaEntrada, CpfEntrada, EstadoConversa, EstoqueSaida, EtapaEntrada, ExtratoEntrada, ExtratoPontosSaida,
    LembreteEntrada, LembreteSaida, LinkCarrinhoSaida, LojasDaMarca, MensagemNoChamadoEntrada, MeusPedidosSaida,
    MeusPontosSaida, PedidosEntrada, PoliticasSaida, RascunhoDoChamadoEntrada, RascunhoSaida, ResumoDoN8nEntrada,
    TextoDaConversaEntrada, VocabularioSaida,
)
from src.entities.atendimento import MensagemSaida
from src.entities.comum import Lista, Marca
from src.use_cases import assistente, assistente_conta, assistente_politicas, resumo_conversa
from src.use_cases.horarios import BRASILIA
from src.utils.supabase_storage import SupabaseStorage
from src.utils.upload import ler_upload

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
        404: {"description": "Peça não encontrada (pedido de uma peça só)"},
        422: {"description": "Nenhuma peça com estoque para o site (a mensagem diz em quais lojas tem)"},
    },
    description="Link que abre o site com as peças escolhidas na conversa já no carrinho (repita id_variante para "
                "cada peça). Só entra peça com estoque para o site; as outras vêm em fora_do_link, com o motivo.",
)
def link_carrinho(
    id_variante: list[int] = Query(min_length=1, max_length=10, description="Uma ou mais peças, até 10"),
    conversa: tuple = Depends(conversa_do_cabecalho),
    db: Session = Depends(get_db),
):
    return resumo_conversa.com_assunto(db, *conversa, "carrinho", lambda: assistente.link_carrinho(db, id_variante))


@router.get(
    "/lojas",
    response_model=Lista[LojasDaMarca],
    description="Lojas por marca, com endereço, o horário de hoje em texto pronto e o dos próximos 7 dias, já com "
                "as exceções cadastradas e os feriados nacionais.",
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


# sem assunto no resumo anônimo: a lista de assuntos é fixa no banco e ainda não tem o de políticas
@router.get(
    "/politicas",
    response_model=PoliticasSaida,
    description="Regras por seção, em texto pronto: pontos de cada marca (regra vigente), entrega e retirada, troca "
                "e devolução, pagamento e conta. Os números saem das mesmas constantes que as regras usam.",
)
def politicas(db: Session = Depends(get_db)):
    return assistente_politicas.politicas(db)


# ---------- conversa e confirmação por e-mail (fluxo fixo do n8n, sem IA) ----------

@router.post(
    "/conversa",
    response_model=EstadoConversa,
    description="Registra a mensagem do cliente: chamada no começo de toda mensagem. Diz a etapa, se a conta "
                "está confirmada, se a conversa é nova (30 min parada: apagar a memória curta), o limite do dia, "
                "se a equipe está conversando no chamado (respondeu há menos de 30 min: a IA não responde) "
                "e, fora do horário da equipe, quando ela volta.",
)
def registrar_mensagem(dados: ConversaEntrada, db: Session = Depends(get_db)):
    return assistente_conta.registrar_mensagem(db, dados.canal, dados.id_externo, datetime.now(BRASILIA))


@router.put(
    "/conversa/etapa",
    response_model=EstadoConversa,
    responses={404: {"description": "Conversa não registrada"}},
    description="Põe a conversa esperando o CPF (o cliente pediu pontos ou pedidos) ou a devolve para a IA "
                "(desistiu, ou apertou [Corrigir] no resumo do chamado, que é desfeito).",
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
    "/conversa/mensagens",
    status_code=204,
    responses={404: {"description": "Conversa não registrada"}},
    description="Guarda, depois de cada resposta, a mensagem do cliente e a resposta do assistente, por 90 dias. "
                "CPF e código saem mascarados; o texto passa para a conta quando o cliente se confirma.",
)
def guardar_texto(dados: TextoDaConversaEntrada, db: Session = Depends(get_db)):
    assistente_conta.guardar_texto(db, dados.canal, dados.id_externo, dados.cliente, dados.assistente,
                                   datetime.now(BRASILIA))


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
                "o que foi entregue (auditoria). oferecer_lembrete diz se o assistente oferece o e-mail antes "
                "de os pontos vencerem (uma vez por conta).",
)
def meus_pontos(dados: ConversaEntrada, db: Session = Depends(get_db)):
    return resumo_conversa.com_assunto(
        db, dados.canal, dados.id_externo, "pontos",
        lambda: assistente_conta.meus_pontos(db, dados.canal, dados.id_externo, datetime.now(BRASILIA)),
    )


@router.put(
    "/lembrete-pontos",
    response_model=LembreteSaida,
    responses={
        401: {"description": "Conta não confirmada nesta conversa: pedir o CPF"},
        422: {"description": "Cliente sem conta no site (a mensagem traz o link de cadastro)"},
    },
    description="Guarda o aceite (ou a recusa) do e-mail antes de os pontos vencerem. Recusar desliga um aceite "
                "anterior.",
)
def responder_lembrete(dados: LembreteEntrada, db: Session = Depends(get_db)):
    return resumo_conversa.com_assunto(
        db, dados.canal, dados.id_externo, "pontos",
        lambda: assistente_conta.responder_lembrete(db, dados.canal, dados.id_externo, dados.aceita,
                                                    datetime.now(BRASILIA)),
    )


@router.post(
    "/avisos/pontos-vencendo",
    response_model=AvisoPontosSaida,
    description="Manda o e-mail de pontos vencendo nos próximos 14 dias a quem aceitou o lembrete, um aviso por "
                "lote. Chamada uma vez por dia pelo pg_cron do banco.",
)
def avisar_pontos_vencendo(db: Session = Depends(get_db)):
    return assistente_conta.avisar_pontos_vencendo(db, datetime.now(BRASILIA))


@router.post(
    "/meus-pedidos",
    response_model=MeusPedidosSaida,
    responses={
        401: {"description": "Conta não confirmada nesta conversa: pedir o CPF"},
        404: {"description": "Pedido não encontrado"},
    },
    description="Últimos 5 pedidos (ou um): status, onde e até quando retirar, até quando trocar, devolução, motivo "
                "do cancelamento e os pagamentos e estornos. Fica registrado o que foi entregue (auditoria).",
)
def meus_pedidos(dados: PedidosEntrada, db: Session = Depends(get_db)):
    return resumo_conversa.com_assunto(
        db, dados.canal, dados.id_externo, "pedidos",
        lambda: assistente_conta.meus_pedidos(db, dados.canal, dados.id_externo, dados.id_pedido,
                                              datetime.now(BRASILIA)),
    )


@router.post(
    "/extrato-pontos",
    response_model=ExtratoPontosSaida,
    responses={401: {"description": "Conta não confirmada nesta conversa: pedir o CPF"}},
    description="Últimos 10 movimentos de pontos (ganhos, usados, estornos, vencidos), por marca se o cliente falar "
                "uma. Responde \"para onde foram meus pontos?\". Fica registrado o que foi entregue (auditoria).",
)
def extrato_pontos(dados: ExtratoEntrada, db: Session = Depends(get_db)):
    return resumo_conversa.com_assunto(
        db, dados.canal, dados.id_externo, "pontos",
        lambda: assistente_conta.extrato_pontos(db, dados.canal, dados.id_externo, dados.marca,
                                                datetime.now(BRASILIA)),
    )


# ---------- chamado: passar para uma pessoa (a conversa continua no canal) ----------

@router.put(
    "/chamado/rascunho",
    response_model=RascunhoSaida,
    responses={
        401: {"description": "Conta não confirmada nesta conversa: pedir o CPF"},
        404: {"description": "Pedido não encontrado"},
        409: {"description": "A conversa já tem um chamado aberto"},
        422: {"description": "Cliente sem conta no site (a mensagem traz o link de cadastro)"},
    },
    description="Guarda o resumo que a IA montou e põe a conversa em confirmando_chamado. A mensagem volta pronta "
                "para mostrar com os botões [Enviar] [Corrigir]; [Corrigir] devolve a conversa para a IA "
                "(PUT /conversa/etapa livre). Chamar de novo troca o rascunho.",
)
def rascunhar_chamado(dados: RascunhoDoChamadoEntrada, db: Session = Depends(get_db)):
    return resumo_conversa.com_assunto(
        db, dados.canal, dados.id_externo, "chamado",
        lambda: assistente_conta.rascunhar_chamado(
            db, dados.canal, dados.id_externo, dados.model_dump(include={"categoria", "assunto", "resumo",
                                                                         "id_pedido"}),
            datetime.now(BRASILIA),
        ),
    )


@router.post(
    "/chamado",
    status_code=201,
    response_model=ChamadoAbertoSaida,
    responses={
        401: {"description": "Conta não confirmada nesta conversa: pedir o CPF"},
        409: {"description": "A conversa já tem um chamado aberto"},
        422: {"description": "Sem resumo esperando o [Enviar], ou cliente sem conta no site"},
    },
    description="Abre o chamado depois do botão [Enviar], com o rascunho guardado e a conversa. Daqui em diante a "
                "conversa fica em_chamado: cada mensagem do cliente vai também para o chamado.",
)
def abrir_chamado(dados: ChamadoDoAssistenteEntrada, db: Session = Depends(get_db)):
    return resumo_conversa.com_assunto(
        db, dados.canal, dados.id_externo, "chamado",
        lambda: assistente_conta.abrir_chamado(
            db, dados.canal, dados.id_externo, dados.model_dump()["conversa"], datetime.now(BRASILIA),
        ),
    )


@router.post(
    "/chamado/mensagem",
    status_code=201,
    response_model=MensagemSaida,
    responses={
        404: {"description": "Conversa não registrada"},
        422: {"description": "A conversa não tem chamado aberto (foi concluído: a etapa volta a livre)"},
    },
    description="Grava no chamado aberto a mensagem do cliente, para o atendente ver a conversa inteira.",
)
def mensagem_no_chamado(dados: MensagemNoChamadoEntrada, db: Session = Depends(get_db)):
    return assistente_conta.mensagem_no_chamado(db, dados.canal, dados.id_externo, dados.texto)


@router.post(
    "/chamado/anexo",
    status_code=201,
    response_model=MensagemSaida,
    responses={
        404: {"description": "Conversa não registrada"},
        422: {"description": "A conversa não tem chamado aberto, ou o arquivo não é JPG, PNG, WEBP ou PDF de até 10 MB"},
        503: {"description": "Storage indisponível"},
    },
    description="Grava no chamado aberto a foto ou o PDF que o cliente mandou no canal, em multipart/form-data, "
                "como o anexo enviado pelo site; o texto (legenda) é opcional.",
)
def anexo_no_chamado(
    canal: Literal["whatsapp", "telegram"] = Form(), id_externo: str = Form(min_length=1, max_length=100),
    arquivo: UploadFile = File(), conteudo: str | None = Form(None, max_length=5000),
    db: Session = Depends(get_db), storage: SupabaseStorage = Depends(get_storage),
):
    return assistente_conta.anexo_no_chamado(
        db, storage, canal, id_externo, ler_upload(arquivo), (conteudo or "").strip() or None,
    )
