from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from src.database.session import get_db
from src.entities.comum import LIMITE_MAXIMO, LIMITE_PADRAO, Lista, Pagina
from src.entities.estoque import (
    Canal, Divergencia, EmTransitoItem, EstoqueHistoricoItem, EstoqueItem, Evolucao, Granularidade, MovimentacaoItem,
    MovimentacaoSaida, NovaMovimentacao, NovaRealocacao, NovoMinimo,
)
from src.middlewares.permissoes import exige_permissao
from src.models.contas import Usuario
from src.use_cases import estoque

router = APIRouter(tags=["estoque"])

# a página Estoque aparece para quem tem alguma permissão da área (case, seções 6 e 8)
VER_ESTOQUE = exige_permissao(
    "movimentar_estoque", "definir_estoque_minimo", "solicitar_transferencia", "enviar_transferencia",
    "receber_transferencia",
)
ERROS_COMUNS = {401: {"description": "Sem login válido"}, 403: {"description": "Sem permissão"}}
ERROS_DE_ESCRITA = {
    **ERROS_COMUNS,
    404: {"description": "Variante ou unidade não encontrada"},
    422: {"description": "Dados inválidos ou regra do estoque (ex.: disponível insuficiente)"},
}


def _paginacao(limit: int = Query(LIMITE_PADRAO, ge=1, le=LIMITE_MAXIMO), offset: int = Query(0, ge=0)):
    return {"limit": limit, "offset": offset}


@router.get("/estoque", response_model=Pagina[EstoqueItem], responses=ERROS_COMUNS)
def listar_estoque(
    id_unidade: int | None = None,
    id_variante: int | None = None,
    canal: Canal | None = None,
    busca: str | None = Query(None, description="Parte do SKU ou do nome do produto"),
    abaixo_minimo: bool | None = Query(None, description="Só as linhas abaixo (true) ou não abaixo (false) do mínimo"),
    paginacao: dict = Depends(_paginacao),
    _: Usuario = Depends(VER_ESTOQUE),
    db: Session = Depends(get_db),
):
    return estoque.listar_estoque(
        db, **paginacao, id_unidade=id_unidade, id_variante=id_variante, canal=canal, busca=busca,
        abaixo_minimo=abaixo_minimo,
    )


@router.get("/estoque/historico", response_model=Pagina[EstoqueHistoricoItem], responses=ERROS_COMUNS)
def estoque_em(
    em: str = Query(description="AAAA-MM-DD (fim do dia) ou AAAA-MM-DDTHH:MM, no horário de Brasília"),
    id_unidade: int | None = None,
    id_variante: int | None = None,
    canal: Canal | None = None,
    busca: str | None = None,
    paginacao: dict = Depends(_paginacao),
    _: Usuario = Depends(VER_ESTOQUE),
    db: Session = Depends(get_db),
):
    return estoque.estoque_em(
        db, em, **paginacao, id_unidade=id_unidade, id_variante=id_variante, canal=canal, busca=busca
    )


@router.get(
    "/estoque/em-transito",
    response_model=Lista[EmTransitoItem],
    responses=ERROS_COMUNS,
    description="Peças enviadas e ainda não recebidas naquele momento: completam o histórico da rede inteira.",
)
def em_transito(
    em: str | None = Query(None, description="AAAA-MM-DD (fim do dia) ou AAAA-MM-DDTHH:MM. Padrão: agora"),
    id_variante: int | None = None,
    id_unidade: int | None = Query(None, description="Transferências que saem ou chegam nesta unidade"),
    busca: str | None = None,
    _: Usuario = Depends(VER_ESTOQUE),
    db: Session = Depends(get_db),
):
    return {"items": estoque.em_transito(db, em, id_variante=id_variante, id_unidade=id_unidade, busca=busca)}


@router.get("/estoque/evolucao", response_model=Evolucao, responses=ERROS_COMUNS)
def evolucao(
    id_variante: int,
    id_unidade: int | None = Query(None, description="Vazio: soma de todas as unidades"),
    inicio: str | None = Query(None, description="Padrão: 30 dias antes do fim"),
    fim: str | None = Query(None, description="Padrão: agora"),
    granularidade: Granularidade | None = Query(None, description="Padrão: escolhida pelo tamanho do intervalo"),
    _: Usuario = Depends(VER_ESTOQUE),
    db: Session = Depends(get_db),
):
    return estoque.evolucao(db, id_variante, id_unidade, inicio, fim, granularidade)


@router.get(
    "/estoque/divergencias",
    response_model=list[Divergencia],
    responses=ERROS_COMUNS,
    description="Linhas em que o saldo não bate com a soma das movimentações. Deve vir sempre vazia (ADR 0005).",
)
def divergencias(_: Usuario = Depends(VER_ESTOQUE), db: Session = Depends(get_db)):
    return estoque.listar_divergencias(db)


@router.put("/estoque/minimo", response_model=EstoqueItem, responses=ERROS_DE_ESCRITA)
def definir_minimo(
    dados: NovoMinimo,
    usuario: Usuario = Depends(exige_permissao("definir_estoque_minimo")),
    db: Session = Depends(get_db),
):
    return estoque.definir_minimo(
        db, usuario, dados.id_variante, dados.id_unidade, dados.canal, dados.estoque_minimo
    )


@router.get("/movimentacoes-estoque", response_model=Pagina[MovimentacaoItem], responses=ERROS_COMUNS)
def listar_movimentacoes(
    id_unidade: int | None = None,
    id_variante: int | None = None,
    canal: Canal | None = None,
    tipo: str | None = None,
    de: str | None = Query(None, description="AAAA-MM-DD ou AAAA-MM-DDTHH:MM"),
    ate: str | None = Query(None, description="AAAA-MM-DD (fim do dia) ou AAAA-MM-DDTHH:MM"),
    busca: str | None = None,
    paginacao: dict = Depends(_paginacao),
    _: Usuario = Depends(VER_ESTOQUE),
    db: Session = Depends(get_db),
):
    return estoque.listar_movimentacoes(
        db, **paginacao, de=de, ate=ate, id_unidade=id_unidade, id_variante=id_variante, canal=canal,
        tipo=tipo, busca=busca,
    )


@router.post(
    "/movimentacoes-estoque",
    status_code=status.HTTP_201_CREATED,
    response_model=MovimentacaoSaida,
    responses=ERROS_DE_ESCRITA,
)
def registrar_movimentacao(
    dados: NovaMovimentacao,
    usuario: Usuario = Depends(exige_permissao("movimentar_estoque")),
    db: Session = Depends(get_db),
):
    return estoque.registrar_movimentacao(
        db, usuario, dados.id_variante, dados.id_unidade, dados.canal, dados.tipo, dados.quantidade, dados.motivo
    )


@router.post(
    "/realocacoes",
    status_code=status.HTTP_201_CREATED,
    response_model=list[MovimentacaoSaida],
    responses=ERROS_DE_ESCRITA,
    description="Passa peças disponíveis de um canal para o outro na mesma loja (saída e entrada juntas).",
)
def realocar(
    dados: NovaRealocacao,
    usuario: Usuario = Depends(exige_permissao("movimentar_estoque")),
    db: Session = Depends(get_db),
):
    return estoque.realocar(db, usuario, dados.id_variante, dados.id_unidade, dados.canal_origem, dados.quantidade)
