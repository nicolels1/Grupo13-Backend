from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from sqlalchemy.orm import Session

from src.database.session import get_db
from src.entities.atendimento import (
    Categoria, ChamadoAlterar, ChamadoConcluir, ChamadoCriar, ChamadoSaida, HistoricoSaida,
    DevolucaoCriar, EstornoPedido, MensagemClienteCriar, MensagemEquipeCriar, MensagemSaida, Prioridade, Status,
    TrocaCriar,
)
from src.entities.arquivos import AnexoLink
from src.entities.comum import LIMITE_MAXIMO, LIMITE_PADRAO, Lista, Pagina, campos_alterados
from src.middlewares.permissoes import exige_cliente, exige_permissao
from src.models.contas import Usuario
from src.entities.vendas import PedidoSaida
from src.use_cases import atendimento, devolucoes
from src.utils.supabase_storage import SupabaseStorage
from src.utils.upload import get_storage, ler_upload

router = APIRouter(tags=["atendimento"])

ATENDER = exige_permissao("atender_chamado")
ERROS_LEITURA = {401: {"description": "Sem login válido"}, 403: {"description": "Sem permissão"},
                 404: {"description": "Chamado não encontrado"}}
ERROS_ESCRITA = {**ERROS_LEITURA, 409: {"description": "Chamado já tem responsável"},
                 422: {"description": "Dados inválidos ou chamado concluído"}}


ERROS_ANEXO = {**ERROS_ESCRITA, 404: {"description": "Chamado ou anexo não encontrado"},
               503: {"description": "Storage indisponível"}}


# texto opcional que acompanha o anexo: vazio vira nenhum
def _texto(conteudo: str | None) -> str | None:
    return (conteudo or "").strip() or None


def _paginacao(limit: int = Query(LIMITE_PADRAO, ge=1, le=LIMITE_MAXIMO), offset: int = Query(0, ge=0)):
    return {"limit": limit, "offset": offset}


# ---------- plataforma do cliente: só os chamados da própria conta ----------

@router.post("/chamados", status_code=status.HTTP_201_CREATED, response_model=ChamadoSaida, responses=ERROS_ESCRITA)
def abrir_chamado(dados: ChamadoCriar, cliente: Usuario = Depends(exige_cliente), db: Session = Depends(get_db)):
    return atendimento.abrir_chamado(db, cliente, dados.model_dump())


@router.get("/chamados", response_model=Pagina[ChamadoSaida], responses=ERROS_LEITURA)
def meus_chamados(
    status_chamado: Status | None = Query(None, alias="status"),
    paginacao: dict = Depends(_paginacao),
    cliente: Usuario = Depends(exige_cliente),
    db: Session = Depends(get_db),
):
    return atendimento.listar_chamados_do_cliente(db, cliente, **paginacao, status=status_chamado)


@router.get("/chamados/{id_chamado}", response_model=ChamadoSaida, responses=ERROS_LEITURA)
def meu_chamado(id_chamado: int, cliente: Usuario = Depends(exige_cliente), db: Session = Depends(get_db)):
    return atendimento.detalhar_chamado_do_cliente(db, cliente, id_chamado)


@router.get(
    "/chamados/{id_chamado}/mensagens",
    response_model=Lista[MensagemSaida],
    responses=ERROS_LEITURA,
    description="Mensagens visíveis ao cliente (as internas da equipe ficam de fora). Marca as da equipe como lidas.",
)
def mensagens_do_meu_chamado(id_chamado: int, cliente: Usuario = Depends(exige_cliente), db: Session = Depends(get_db)):
    return {"items": atendimento.mensagens_do_cliente(db, cliente, id_chamado)}


@router.post(
    "/chamados/{id_chamado}/mensagens",
    status_code=status.HTTP_201_CREATED,
    response_model=MensagemSaida,
    responses=ERROS_ESCRITA,
)
def responder_meu_chamado(
    id_chamado: int, dados: MensagemClienteCriar, cliente: Usuario = Depends(exige_cliente),
    db: Session = Depends(get_db),
):
    return atendimento.enviar_mensagem_do_cliente(db, cliente, id_chamado, dados.conteudo)


@router.post(
    "/chamados/{id_chamado}/anexos",
    status_code=status.HTTP_201_CREATED,
    response_model=MensagemSaida,
    responses=ERROS_ANEXO,
    description="Mensagem com anexo (JPG, PNG, WEBP ou PDF de até 10 MB), em multipart/form-data; o texto é opcional.",
)
def anexar_no_meu_chamado(
    id_chamado: int, arquivo: UploadFile = File(), conteudo: str | None = Form(None, max_length=5000),
    cliente: Usuario = Depends(exige_cliente), db: Session = Depends(get_db),
    storage: SupabaseStorage = Depends(get_storage),
):
    return atendimento.anexar_do_cliente(db, storage, cliente, id_chamado, ler_upload(arquivo), _texto(conteudo))


@router.get(
    "/chamados/{id_chamado}/mensagens/{id_mensagem}/anexo",
    response_model=AnexoLink,
    responses=ERROS_ANEXO,
    description="Link temporário para abrir o anexo (área privada do Storage).",
)
def anexo_do_meu_chamado(
    id_chamado: int, id_mensagem: int, cliente: Usuario = Depends(exige_cliente), db: Session = Depends(get_db),
    storage: SupabaseStorage = Depends(get_storage),
):
    return atendimento.anexo_para_o_cliente(db, storage, cliente, id_chamado, id_mensagem)


# ---------- plataforma interna: permissão atender_chamado ----------

@router.get("/atendimento/chamados", response_model=Pagina[ChamadoSaida], responses=ERROS_LEITURA)
def fila(
    status_chamado: Status | None = Query(None, alias="status"),
    sem_responsavel: bool | None = Query(None, description="true: a fila de chamados que ninguém assumiu"),
    meus: bool = Query(False, description="Só os chamados em que sou responsável"),
    com_mensagem_nova: bool | None = Query(None, description="Com mensagem do cliente ainda não lida"),
    categoria: Categoria | None = None,
    prioridade: Prioridade | None = None,
    id_unidade: int | None = None,
    paginacao: dict = Depends(_paginacao),
    usuario: Usuario = Depends(ATENDER),
    db: Session = Depends(get_db),
):
    return atendimento.listar_fila(
        db, usuario, **paginacao, meus=meus, status=status_chamado, sem_responsavel=sem_responsavel,
        com_mensagem_nova=com_mensagem_nova, categoria=categoria, prioridade=prioridade, id_unidade=id_unidade,
    )


@router.get("/atendimento/chamados/{id_chamado}", response_model=ChamadoSaida, responses=ERROS_LEITURA)
def detalhar(id_chamado: int, _: Usuario = Depends(ATENDER), db: Session = Depends(get_db)):
    return atendimento.detalhar_chamado(db, id_chamado)


@router.post("/atendimento/chamados/{id_chamado}/assumir", response_model=ChamadoSaida, responses=ERROS_ESCRITA)
def assumir(id_chamado: int, usuario: Usuario = Depends(ATENDER), db: Session = Depends(get_db)):
    return atendimento.assumir(db, usuario, id_chamado)


@router.patch(
    "/atendimento/chamados/{id_chamado}",
    response_model=ChamadoSaida,
    responses=ERROS_ESCRITA,
    description="Só o responsável: define a prioridade e/ou repassa o chamado para outra pessoa que atende.",
)
def alterar(
    id_chamado: int, dados: ChamadoAlterar, usuario: Usuario = Depends(ATENDER), db: Session = Depends(get_db)
):
    return atendimento.alterar(db, usuario, id_chamado, campos_alterados(dados))


@router.post("/atendimento/chamados/{id_chamado}/concluir", response_model=ChamadoSaida, responses=ERROS_ESCRITA)
def concluir(
    id_chamado: int, dados: ChamadoConcluir, usuario: Usuario = Depends(ATENDER), db: Session = Depends(get_db)
):
    return atendimento.concluir(db, usuario, id_chamado, dados.motivo)


@router.get(
    "/atendimento/chamados/{id_chamado}/mensagens",
    response_model=Lista[MensagemSaida],
    responses=ERROS_LEITURA,
    description="Todas as mensagens, inclusive as internas. Marca as do cliente como lidas.",
)
def mensagens(id_chamado: int, _: Usuario = Depends(ATENDER), db: Session = Depends(get_db)):
    return {"items": atendimento.mensagens_da_equipe(db, id_chamado)}


@router.post(
    "/atendimento/chamados/{id_chamado}/mensagens",
    status_code=status.HTTP_201_CREATED,
    response_model=MensagemSaida,
    responses=ERROS_ESCRITA,
)
def responder(
    id_chamado: int, dados: MensagemEquipeCriar, usuario: Usuario = Depends(ATENDER), db: Session = Depends(get_db)
):
    return atendimento.enviar_mensagem_da_equipe(db, usuario, id_chamado, dados.conteudo, dados.interna)


@router.post(
    "/atendimento/chamados/{id_chamado}/anexos",
    status_code=status.HTTP_201_CREATED,
    response_model=MensagemSaida,
    responses=ERROS_ANEXO,
    description="Mensagem com anexo, em multipart/form-data; o texto é opcional e interna esconde do cliente.",
)
def anexar(
    id_chamado: int, arquivo: UploadFile = File(), conteudo: str | None = Form(None, max_length=5000),
    interna: bool = Form(False), usuario: Usuario = Depends(ATENDER), db: Session = Depends(get_db),
    storage: SupabaseStorage = Depends(get_storage),
):
    return atendimento.anexar_da_equipe(db, storage, usuario, id_chamado, ler_upload(arquivo), _texto(conteudo), interna)


@router.get(
    "/atendimento/chamados/{id_chamado}/mensagens/{id_mensagem}/anexo",
    response_model=AnexoLink,
    responses=ERROS_ANEXO,
)
def anexo(
    id_chamado: int, id_mensagem: int, _: Usuario = Depends(ATENDER), db: Session = Depends(get_db),
    storage: SupabaseStorage = Depends(get_storage),
):
    return atendimento.anexo_para_a_equipe(db, storage, id_chamado, id_mensagem)


@router.get("/atendimento/chamados/{id_chamado}/historico", response_model=Lista[HistoricoSaida], responses=ERROS_LEITURA)
def historico(id_chamado: int, _: Usuario = Depends(ATENDER), db: Session = Depends(get_db)):
    return {"items": atendimento.historico(db, id_chamado)}


# ---------- troca, devolução e estorno do pedido do chamado (atender_chamado) ----------

ERROS_PEDIDO = {**ERROS_ESCRITA, 404: {"description": "Chamado, loja, variante ou pagamento não encontrado"},
                422: {"description": "Fora do prazo de 30 dias, pedido não entregue ou peças a mais"}}


@router.post(
    "/atendimento/chamados/{id_chamado}/devolucao",
    response_model=PedidoSaida,
    responses=ERROS_PEDIDO,
    description="A peça volta ao estoque de loja física da loja que recebeu, com estorno ligado ao chamado.",
)
def devolver(
    id_chamado: int, dados: DevolucaoCriar, usuario: Usuario = Depends(ATENDER), db: Session = Depends(get_db),
):
    return devolucoes.registrar_devolucao(db, usuario, id_chamado, dados.model_dump())


@router.post(
    "/atendimento/chamados/{id_chamado}/troca",
    response_model=PedidoSaida,
    responses=ERROS_PEDIDO,
    description="A peça devolvida entra e a nova sai do estoque de loja física da mesma loja; sem estorno.",
)
def trocar(id_chamado: int, dados: TrocaCriar, usuario: Usuario = Depends(ATENDER), db: Session = Depends(get_db)):
    return devolucoes.registrar_troca(db, usuario, id_chamado, dados.model_dump())


@router.post(
    "/atendimento/chamados/{id_chamado}/estornos",
    response_model=PedidoSaida,
    responses=ERROS_PEDIDO,
    description="Estorno sem troca nem devolução (ex.: problema na entrega). Pode ser parcial.",
)
def estornar(
    id_chamado: int, dados: EstornoPedido, usuario: Usuario = Depends(ATENDER), db: Session = Depends(get_db),
):
    return devolucoes.registrar_estorno(db, usuario, id_chamado, dados.id_pagamento, dados.valor)
