from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import get_args

from sqlalchemy.orm import Session

from src.entities.comum import Marca
from src.models.contas import Usuario
from src.repositories import catalogo_repository, permissao_repository, unidade_repository
from src.repositories import resumo_repository as repo
from src.use_cases import fidelidade
from src.use_cases.catalogo import foto_principal
from src.use_cases.erros import RecursoNaoEncontrado, RegraDeNegocio, SemPermissao
from src.use_cases.estoque import BRASILIA
from src.use_cases.permissoes import usuario_tem_permissao

MARCAS = get_args(Marca)
# canal da venda (P3); o app só existe no histórico da Vulto e só aparece quando o período tem venda dele
CANAIS = ("loja", "site")
APP = "app"
VENDAS = ("registrar_venda_fisica", "preparar_entregar_pedido")
ZERO = Decimal("0.00")
DIAS_DE_VENDAS = 14
DIAS_DA_SEMANA = 7
DIAS_DO_MES = 30
DIAS_DE_AVALIACOES = 90
RETIRADA_PERTO_DE_VENCER = timedelta(days=5)  # a retirada vence em 7 dias (case, seção 5)
MAIS_VENDIDAS = 5


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _hoje() -> date:
    return _agora().astimezone(BRASILIA).date()


# "últimos N dias": hoje e os N-1 dias anteriores, inteiros, no horário de Brasília
def inicio_dos_ultimos(dias: int, hoje: date | None = None) -> datetime:
    return datetime.combine((hoje or _hoje()) - timedelta(days=dias - 1), time.min, BRASILIA)


# meia-noite seguinte ao dia: as janelas terminam no fim do dia de referência
def fim_do_dia(dia: date) -> datetime:
    return datetime.combine(dia + timedelta(days=1), time.min, BRASILIA)


def _dias(dias: int, hoje: date) -> list[date]:
    return [hoje - timedelta(days=d) for d in range(dias - 1, -1, -1)]


def _uma_casa(valor) -> float | None:
    return None if valor is None else float(Decimal(str(valor)).quantize(Decimal("0.1"), ROUND_HALF_UP))


# saldo ÷ média diária de peças vendidas nos últimos 30 dias; sem venda, não há cobertura
def cobertura_dias(saldo: int, vendidas_30_dias: int) -> float | None:
    if not vendidas_30_dias:
        return None
    return _uma_casa(saldo / (vendidas_30_dias / DIAS_DO_MES))


def variacao_pct(atual, anterior) -> float | None:
    if not anterior:
        return None
    return _uma_casa((Decimal(atual) - Decimal(anterior)) * 100 / Decimal(anterior))


def _canais(*grupos_de_linhas: list) -> tuple[str, ...]:
    tem_app = any(linha["canal_venda"] == APP for linhas in grupos_de_linhas for linha in linhas)
    return CANAIS + (APP,) if tem_app else CANAIS


def _volume(linha) -> dict:
    return {"pedidos": linha["pedidos"], "valor": linha["valor"] or ZERO} if linha else {"pedidos": 0, "valor": ZERO}


def _por_canal(linhas: list, canais: tuple[str, ...]) -> dict[str, dict]:
    por_canal = {linha["canal_venda"]: linha for linha in linhas}
    totais = {canal: _volume(por_canal.get(canal)) for canal in canais}
    totais["total"] = {"pedidos": sum(t["pedidos"] for t in totais.values()),
                       "valor": sum((t["valor"] for t in totais.values()), ZERO)}
    return totais


def _com_variacao(atual: dict, anterior: dict) -> dict:
    return {**atual, "variacao_pedidos_pct": variacao_pct(atual["pedidos"], anterior["pedidos"]),
            "variacao_valor_pct": variacao_pct(atual["valor"], anterior["valor"])}


def _ticket(volume: dict) -> Decimal | None:
    return (volume["valor"] / volume["pedidos"]).quantize(Decimal("0.01")) if volume["pedidos"] else None


# ---------- seções ----------

def _vendas_por_dia(db: Session, id_unidade: int | None, marca: str | None, hoje: date) -> list[dict]:
    linhas = repo.vendas_por_dia(db, inicio_dos_ultimos(DIAS_DE_VENDAS, hoje), fim_do_dia(hoje), id_unidade, marca)
    por_dia = {(linha["dia"], linha["canal_venda"]): linha for linha in linhas}
    canais = _canais(linhas)
    return [{"dia": dia, **{canal: _volume(por_dia.get((dia, canal))) for canal in canais}}
            for dia in _dias(DIAS_DE_VENDAS, hoje)]


def _mais_vendidas(db: Session, id_unidade: int | None, marca: str | None, hoje: date) -> list[dict]:
    linhas = repo.mais_vendidas(db, inicio_dos_ultimos(DIAS_DA_SEMANA, hoje), fim_do_dia(hoje), id_unidade, marca,
                                MAIS_VENDIDAS)
    saldos = repo.saldos_das_variantes(db, [l["id_variante"] for l in linhas], id_unidade)
    imagens = catalogo_repository.imagens_dos_produtos(db, list({l["id_produto"] for l in linhas}))
    return [{
        "id_variante": l["id_variante"], "produto": l["produto"], "cor": l["cor"], "tamanho": l["tamanho"],
        "sku": l["sku"], "foto_url": foto_principal(imagens[l["id_produto"]], l["cor"]),
        "quantidade_vendida": int(l["quantidade_vendida"]), "saldo_atual": saldos.get(l["id_variante"], 0),
    } for l in linhas]


def _chamados(db: Session, id_unidade: int | None, marca: str | None, hoje: date) -> dict:
    desde, ate = inicio_dos_ultimos(DIAS_DA_SEMANA, hoje), fim_do_dia(hoje)
    por_status = repo.chamados_por_status(db, desde, ate, id_unidade, marca)
    abertos, concluidos = repo.chamados_por_dia(db, desde, ate, id_unidade, marca)
    return {
        "abertos": por_status.get("aberto", 0), "em_andamento": por_status.get("em_andamento", 0),
        "concluidos_7_dias": por_status.get("concluido", 0),
        "por_dia": [{"dia": dia, "abertos": abertos.get(dia, 0), "concluidos": concluidos.get(dia, 0)}
                    for dia in _dias(DIAS_DA_SEMANA, hoje)],
    }


# Casa Lorenzi × Vulto lado a lado: vendas de 14 dias e ticket de 30 de cada marca
def _por_marca(db: Session, marca: str | None, hoje: date) -> list[dict]:
    fim = fim_do_dia(hoje)
    inicio_14 = inicio_dos_ultimos(DIAS_DE_VENDAS, hoje)
    linhas = {
        periodo: {l["marca"]: l for l in repo.vendas_por_marca(db, desde, ate, marca)}
        for periodo, desde, ate in (("atual", inicio_14, fim),
                                    ("anterior", inicio_dos_ultimos(DIAS_DE_VENDAS * 2, hoje), inicio_14),
                                    ("mes", inicio_dos_ultimos(DIAS_DO_MES, hoje), fim))
    }
    return [{
        "marca": m,
        "vendas_14_dias": _com_variacao(_volume(linhas["atual"].get(m)), _volume(linhas["anterior"].get(m))),
        "ticket_medio_30_dias": _ticket(_volume(linhas["mes"].get(m))),
    } for m in ((marca,) if marca else MARCAS)]


# números da rede inteira (ou da marca), só para o Admin (ignora o filtro de unidade). As janelas terminam no
# dia de referência; saldo, ruptura e retiradas são sempre de agora
def _rede_agora(db: Session, marca: str | None, ids_das_unidades: set[int], hoje: date) -> dict:
    fim = fim_do_dia(hoje)
    inicio_14 = inicio_dos_ultimos(DIAS_DE_VENDAS, hoje)
    inicio_30 = inicio_dos_ultimos(DIAS_DO_MES, hoje)

    linhas_atual = repo.vendas_por_canal(db, inicio_14, fim, marca)
    linhas_anterior = repo.vendas_por_canal(db, inicio_dos_ultimos(DIAS_DE_VENDAS * 2, hoje), inicio_14, marca)
    linhas_mes = repo.vendas_por_canal(db, inicio_30, fim, marca)
    canais = _canais(linhas_atual, linhas_anterior, linhas_mes)
    atual, anterior = _por_canal(linhas_atual, canais), _por_canal(linhas_anterior, canais)
    mes = _por_canal(linhas_mes, canais)

    a_venda, sem_online = repo.ruptura_online(db, marca)
    mediana, sem_resposta = repo.primeira_resposta(db, inicio_30, fim, marca)
    media, quantidade, denuncias = repo.avaliacoes(db, inicio_dos_ultimos(DIAS_DE_AVALIACOES, hoje), fim, marca)
    vendidas = sum(repo.pecas_vendidas_por_unidade(db, inicio_30, fim, marca).values())
    retiradas = repo.retiradas_perto_de_vencer(db, _agora() - RETIRADA_PERTO_DE_VENCER)
    return {
        "cobertura_dias": cobertura_dias(repo.saldo_total(db, marca), vendidas),
        "ruptura_online_pct": _uma_casa(sem_online * 100 / a_venda) if a_venda else None,
        "vendas_14_dias": {chave: _com_variacao(atual[chave], anterior[chave]) for chave in atual},
        "ticket_medio_30_dias": {chave: _ticket(mes[chave]) for chave in mes},
        "por_marca": _por_marca(db, marca, hoje),
        "primeira_resposta_mediana_horas": _uma_casa(mediana),
        "chamados_esperando_primeira_resposta": sem_resposta,
        "avaliacoes": {"nota_media_90_dias": _uma_casa(media), "quantidade_90_dias": quantidade,
                       "denuncias_pendentes": denuncias},
        "retiradas_perto_de_vencer": sum(n for u, n in retiradas.items() if marca is None or u in ids_das_unidades),
    }


def _por_unidade(db: Session, unidades: list, hoje: date) -> list[dict]:
    fim = fim_do_dia(hoje)
    vendas = repo.vendas_por_unidade(db, inicio_dos_ultimos(DIAS_DA_SEMANA, hoje), fim)
    vendidas_30 = repo.pecas_vendidas_por_unidade(db, inicio_dos_ultimos(DIAS_DO_MES, hoje), fim)
    saldos = repo.saldo_por_unidade(db)
    abaixo = repo.abaixo_do_minimo_por_unidade(db)
    retiradas = repo.retiradas_perto_de_vencer(db, _agora() - RETIRADA_PERTO_DE_VENCER)
    esperando_envio, chegando = repo.transferencias_por_unidade(db)
    linhas = []
    for unidade in unidades:
        u = unidade.id_unidade
        pedidos, valor = vendas.get(u, (0, ZERO))
        linhas.append({
            "id_unidade": u, "nome": unidade.nome, "tipo": unidade.tipo, "marca": unidade.marca,
            "vendas_7_dias": {"pedidos": pedidos, "valor": valor or ZERO},
            "cobertura_dias": cobertura_dias(saldos.get(u, 0), vendidas_30.get(u, 0)),
            "variantes_abaixo_do_minimo": abaixo.get(u, 0),
            # o CD não faz retirada
            "retiradas_perto_de_vencer": retiradas.get(u, 0) if unidade.tipo == "loja" else None,
            "transferencias_esperando_envio": esperando_envio.get(u, 0),
            "transferencias_chegando": chegando.get(u, 0),
        })
    return linhas


# o quanto cada marca deve em pontos hoje (o passivo não tem histórico, então ignora a referência)
def _pontos(db: Session, marca: str | None) -> list[dict]:
    return [p for p in fidelidade.passivo(db) if marca is None or p["marca"] == marca]


# ---------- faturamento ----------

# por mês, unidade e canal da venda, com a regra dos três números da Vulto; só Admin, como a rede agora
def faturamento(db: Session, usuario: Usuario, de: date, ate: date, id_unidade: int | None = None,
                marca: str | None = None) -> dict:
    if usuario.tipo_conta != "interna" or not permissao_repository.modelo_eh_admin(db, usuario.id_modelo_acesso):
        raise SemPermissao("Só o Admin vê o faturamento")
    if ate < de:
        raise RegraDeNegocio("O fim do período vem antes do início")
    if id_unidade is not None and unidade_repository.buscar_unidade(db, id_unidade) is None:
        raise RecursoNaoEncontrado("Unidade não encontrada")

    linhas = [{**linha, "valor": linha["valor"] or ZERO, "frete": linha["frete"] or ZERO}
              for linha in repo.faturamento_por_mes(db, inicio_dos_ultimos(1, de), fim_do_dia(ate), id_unidade, marca)]
    return {
        "de": de, "ate": ate, "id_unidade": id_unidade, "marca": marca,
        "valor": sum((l["valor"] for l in linhas), ZERO), "frete": sum((l["frete"] for l in linhas), ZERO),
        "items": linhas,
    }


# ---------- resumo ----------

# só as seções que a conta pode ver: as outras ficam ausentes, não zeradas
def resumo(db: Session, usuario: Usuario, id_unidade: int | None, marca: str | None = None,
           referencia: date | None = None) -> dict:
    if usuario.tipo_conta != "interna":
        raise SemPermissao("Só para contas internas")
    if id_unidade is not None and unidade_repository.buscar_unidade(db, id_unidade) is None:
        raise RecursoNaoEncontrado("Unidade não encontrada")
    if referencia is not None and referencia > _hoje():
        raise RegraDeNegocio("A data de referência não pode ser futura")

    hoje = referencia or _hoje()
    dados = {"id_unidade": id_unidade, "marca": marca, "referencia": hoje, "gerado_em": _agora()}
    if usuario_tem_permissao(db, usuario, *VENDAS):
        dados["vendas_por_dia"] = _vendas_por_dia(db, id_unidade, marca, hoje)
        dados["mais_vendidas"] = _mais_vendidas(db, id_unidade, marca, hoje)
    if usuario_tem_permissao(db, usuario, "atender_chamado"):
        dados["chamados"] = _chamados(db, id_unidade, marca, hoje)
    if usuario_tem_permissao(db, usuario, "gerenciar_fidelidade"):
        dados["pontos"] = _pontos(db, marca)
    if permissao_repository.modelo_eh_admin(db, usuario.id_modelo_acesso):
        unidades = repo.unidades_ativas(db, marca)
        dados["rede_agora"] = _rede_agora(db, marca, {u.id_unidade for u in unidades}, hoje)
        dados["por_unidade"] = _por_unidade(db, unidades, hoje)
    return dados
