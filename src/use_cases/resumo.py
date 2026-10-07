from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from src.models.contas import Usuario
from src.repositories import catalogo_repository, permissao_repository, unidade_repository
from src.repositories import resumo_repository as repo
from src.use_cases.arquivos import FOTO_PRODUTO
from src.use_cases.erros import RecursoNaoEncontrado, SemPermissao
from src.use_cases.estoque import BRASILIA
from src.use_cases.permissoes import usuario_tem_permissao
from src.utils.supabase_storage import url_publica

CANAIS = ("online", "loja_fisica")
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


def _por_canal(linhas: list) -> dict[str, dict]:
    totais = {canal: {"pedidos": 0, "valor": ZERO} for canal in CANAIS}
    for linha in linhas:
        totais[linha["canal"]] = {"pedidos": linha["pedidos"], "valor": linha["valor"] or ZERO}
    totais["total"] = {"pedidos": sum(t["pedidos"] for t in totais.values()),
                       "valor": sum((t["valor"] for t in totais.values()), ZERO)}
    return totais


# ---------- seções ----------

def _vendas_por_dia(db: Session, id_unidade: int | None, hoje: date) -> list[dict]:
    linhas = repo.vendas_por_dia(db, inicio_dos_ultimos(DIAS_DE_VENDAS, hoje), id_unidade)
    por_dia = {(linha["dia"], linha["canal"]): linha for linha in linhas}
    resultado = []
    for dia in _dias(DIAS_DE_VENDAS, hoje):
        item = {"dia": dia}
        for canal in CANAIS:
            linha = por_dia.get((dia, canal))
            item[canal] = {"pedidos": linha["pedidos"], "valor": linha["valor"]} if linha \
                else {"pedidos": 0, "valor": ZERO}
        resultado.append(item)
    return resultado


# foto principal: a primeira da cor da variante; senão, a primeira que vale para todas as cores
def _foto(imagens: list, cor: str) -> str | None:
    escolhida = next((i for i in imagens if i.cor and i.cor.lower() == cor.lower()), None)
    escolhida = escolhida or next((i for i in imagens if i.cor is None), None)
    return url_publica(FOTO_PRODUTO.bucket, escolhida.caminho_arquivo) if escolhida else None


def _mais_vendidas(db: Session, id_unidade: int | None, hoje: date) -> list[dict]:
    linhas = repo.mais_vendidas(db, inicio_dos_ultimos(DIAS_DA_SEMANA, hoje), id_unidade, MAIS_VENDIDAS)
    saldos = repo.saldos_das_variantes(db, [l["id_variante"] for l in linhas], id_unidade)
    imagens = catalogo_repository.imagens_dos_produtos(db, list({l["id_produto"] for l in linhas}))
    return [{
        "id_variante": l["id_variante"], "produto": l["produto"], "cor": l["cor"], "tamanho": l["tamanho"],
        "sku": l["sku"], "foto_url": _foto(imagens[l["id_produto"]], l["cor"]),
        "quantidade_vendida": int(l["quantidade_vendida"]), "saldo_atual": saldos.get(l["id_variante"], 0),
    } for l in linhas]


def _chamados(db: Session, id_unidade: int | None, hoje: date) -> dict:
    desde = inicio_dos_ultimos(DIAS_DA_SEMANA, hoje)
    por_status = repo.chamados_por_status(db, desde, id_unidade)
    abertos, concluidos = repo.chamados_por_dia(db, desde, id_unidade)
    return {
        "abertos": por_status.get("aberto", 0), "em_andamento": por_status.get("em_andamento", 0),
        "concluidos_7_dias": por_status.get("concluido", 0),
        "por_dia": [{"dia": dia, "abertos": abertos.get(dia, 0), "concluidos": concluidos.get(dia, 0)}
                    for dia in _dias(DIAS_DA_SEMANA, hoje)],
    }


# números da rede inteira, só para o Admin (ignora o filtro de unidade)
def _rede_agora(db: Session, hoje: date) -> dict:
    inicio_14 = inicio_dos_ultimos(DIAS_DE_VENDAS, hoje)
    inicio_28 = inicio_dos_ultimos(DIAS_DE_VENDAS * 2, hoje)
    inicio_30 = inicio_dos_ultimos(DIAS_DO_MES, hoje)

    atual = _por_canal(repo.vendas_por_canal(db, inicio_14))
    anterior = _por_canal(repo.vendas_por_canal(db, inicio_28, inicio_14))
    vendas_14 = {
        chave: {
            **atual[chave],
            "variacao_pedidos_pct": variacao_pct(atual[chave]["pedidos"], anterior[chave]["pedidos"]),
            "variacao_valor_pct": variacao_pct(atual[chave]["valor"], anterior[chave]["valor"]),
        }
        for chave in atual
    }
    mes = _por_canal(repo.vendas_por_canal(db, inicio_30))
    ticket = {chave: (mes[chave]["valor"] / mes[chave]["pedidos"]).quantize(Decimal("0.01"))
              if mes[chave]["pedidos"] else None for chave in mes}

    a_venda, sem_online = repo.ruptura_online(db)
    mediana, sem_resposta = repo.primeira_resposta(db, inicio_30)
    media, quantidade, denuncias = repo.avaliacoes(db, inicio_dos_ultimos(DIAS_DE_AVALIACOES, hoje))
    vendidas = sum(repo.pecas_vendidas_por_unidade(db, inicio_30).values())
    return {
        "cobertura_dias": cobertura_dias(repo.saldo_total(db), vendidas),
        "ruptura_online_pct": _uma_casa(sem_online * 100 / a_venda) if a_venda else None,
        "vendas_14_dias": vendas_14,
        "ticket_medio_30_dias": ticket,
        "primeira_resposta_mediana_horas": _uma_casa(mediana),
        "chamados_esperando_primeira_resposta": sem_resposta,
        "avaliacoes": {"nota_media_90_dias": _uma_casa(media), "quantidade_90_dias": quantidade,
                       "denuncias_pendentes": denuncias},
        "retiradas_perto_de_vencer": sum(
            repo.retiradas_perto_de_vencer(db, _agora() - RETIRADA_PERTO_DE_VENCER).values()),
    }


def _por_unidade(db: Session, hoje: date) -> list[dict]:
    vendas = repo.vendas_por_unidade(db, inicio_dos_ultimos(DIAS_DA_SEMANA, hoje))
    vendidas_30 = repo.pecas_vendidas_por_unidade(db, inicio_dos_ultimos(DIAS_DO_MES, hoje))
    saldos = repo.saldo_por_unidade(db)
    abaixo = repo.abaixo_do_minimo_por_unidade(db)
    retiradas = repo.retiradas_perto_de_vencer(db, _agora() - RETIRADA_PERTO_DE_VENCER)
    esperando_envio, chegando = repo.transferencias_por_unidade(db)
    linhas = []
    for unidade in repo.unidades_ativas(db):
        u = unidade.id_unidade
        pedidos, valor = vendas.get(u, (0, ZERO))
        linhas.append({
            "id_unidade": u, "nome": unidade.nome, "tipo": unidade.tipo,
            "vendas_7_dias": {"pedidos": pedidos, "valor": valor or ZERO},
            "cobertura_dias": cobertura_dias(saldos.get(u, 0), vendidas_30.get(u, 0)),
            "variantes_abaixo_do_minimo": abaixo.get(u, 0),
            # o CD não faz retirada
            "retiradas_perto_de_vencer": retiradas.get(u, 0) if unidade.tipo == "loja" else None,
            "transferencias_esperando_envio": esperando_envio.get(u, 0),
            "transferencias_chegando": chegando.get(u, 0),
        })
    return linhas


# ---------- resumo ----------

# só as seções que a conta pode ver: as outras ficam ausentes, não zeradas
def resumo(db: Session, usuario: Usuario, id_unidade: int | None) -> dict:
    if usuario.tipo_conta != "interna":
        raise SemPermissao("Só para contas internas")
    if id_unidade is not None and unidade_repository.buscar_unidade(db, id_unidade) is None:
        raise RecursoNaoEncontrado("Unidade não encontrada")

    hoje = _hoje()
    dados = {"id_unidade": id_unidade, "gerado_em": _agora()}
    if usuario_tem_permissao(db, usuario, *VENDAS):
        dados["vendas_por_dia"] = _vendas_por_dia(db, id_unidade, hoje)
        dados["mais_vendidas"] = _mais_vendidas(db, id_unidade, hoje)
    if usuario_tem_permissao(db, usuario, "atender_chamado"):
        dados["chamados"] = _chamados(db, id_unidade, hoje)
    if permissao_repository.modelo_eh_admin(db, usuario.id_modelo_acesso):
        dados["rede_agora"] = _rede_agora(db, hoje)
        dados["por_unidade"] = _por_unidade(db, hoje)
    return dados
