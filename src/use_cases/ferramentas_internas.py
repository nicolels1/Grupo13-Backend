import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Callable, get_args

from sqlalchemy.orm import Session

from src.entities.comum import Marca
from src.models.contas import Usuario
from src.repositories import (
    assistente_interno_repository, estoque_repository, fidelidade_repository, permissao_repository, resumo_repository,
    unidade_repository,
)
from src.use_cases.erros import RecursoNaoEncontrado, RegraDeNegocio, SemPermissao
from src.use_cases.permissoes import conferir_marca, marca_da_consulta, marcas_visiveis, usuario_tem_permissao
from src.use_cases.estoque import BRASILIA
from src.use_cases.resumo import VENDAS, ZERO, fim_do_dia, inicio_dos_ultimos

# ferramentas do assistente interno: o modelo escolhe qual chamar e com quais parâmetros; o backend executa
# com o login da pessoa e devolve os números prontos (e formatados, para a conferência comparar o texto).
# Cada resultado traz "texto": a resposta fixa, sem IA, usada se a conferência dos números falhar

MARCAS = get_args(Marca)
CANAIS_VENDA = ("loja", "site", "app")
NOME_DO_CANAL = {"loja": "loja física", "site": "site", "app": "app"}
DIAS_DA_SEMANA = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")
PERIODO_MAXIMO = 366  # dias


@dataclass
class Ferramenta:
    nome: str
    descricao: str
    parametros: dict  # JSON Schema dos parâmetros
    executar: Callable[[Session, Usuario, dict, date], dict]

    def definicao(self) -> dict:
        return {"name": self.nome, "description": self.descricao, "input_schema": self.parametros}


# ---------- formatos (os mesmos que a resposta deve usar) ----------

def numero(valor: int) -> str:
    return f"{valor:,}".replace(",", ".")


def reais(valor: Decimal) -> str:
    return "R$ " + f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def data(valor: date) -> str:
    return valor.strftime("%d/%m/%Y")


# ---------- períodos: o backend converte, o modelo só escolhe o nome ----------

PERIODOS = ("hoje", "ontem", "esta_semana", "semana_passada", "este_mes", "mes_passado", "ultimos_7_dias",
            "ultimos_30_dias", "intervalo")


# semana de segunda a domingo; "este mês" e "esta semana" vão até hoje
def datas_do_periodo(periodo: str, hoje: date, de: str | None = None, ate: str | None = None) -> tuple[date, date]:
    segunda = hoje - timedelta(days=hoje.weekday())
    primeiro_do_mes = hoje.replace(day=1)
    if periodo == "hoje":
        return hoje, hoje
    if periodo == "ontem":
        return hoje - timedelta(days=1), hoje - timedelta(days=1)
    if periodo == "esta_semana":
        return segunda, hoje
    if periodo == "semana_passada":
        return segunda - timedelta(days=7), segunda - timedelta(days=1)
    if periodo == "este_mes":
        return primeiro_do_mes, hoje
    if periodo == "mes_passado":
        fim = primeiro_do_mes - timedelta(days=1)
        return fim.replace(day=1), fim
    if periodo == "ultimos_7_dias":
        return hoje - timedelta(days=6), hoje
    if periodo == "ultimos_30_dias":
        return hoje - timedelta(days=29), hoje
    if periodo == "intervalo":
        if not de or not ate:
            raise RegraDeNegocio("No intervalo, informe de e ate (AAAA-MM-DD)")
        try:
            inicio, fim = date.fromisoformat(de), date.fromisoformat(ate)
        except ValueError:
            raise RegraDeNegocio("Data inválida: use AAAA-MM-DD")
        if fim < inicio:
            raise RegraDeNegocio("O fim do período vem antes do início")
        if fim > hoje:
            raise RegraDeNegocio("O período não pode terminar no futuro")
        if (fim - inicio).days >= PERIODO_MAXIMO:
            raise RegraDeNegocio("Período maior que um ano: divida em partes")
        return inicio, fim
    raise RegraDeNegocio(f"Período desconhecido: {periodo}")


def _descrever_periodo(inicio: date, fim: date) -> str:
    return f"em {data(inicio)}" if inicio == fim else f"de {data(inicio)} a {data(fim)}"


# a unidade pedida, de uma marca que a pessoa vê; sem unidade, a marca pedida (ou as que ela vê)
def _onde(db: Session, usuario: Usuario, id_unidade: int | None, marca: str | None) -> tuple[str | None, str]:
    visiveis = marcas_visiveis(db, usuario)
    if id_unidade is not None:
        unidade = unidade_repository.buscar_unidade(db, id_unidade)
        if unidade is None:
            raise RecursoNaoEncontrado("Unidade não encontrada")
        conferir_marca(visiveis, {unidade.marca})
        return None, f"na unidade {unidade.nome}"
    marca = marca_da_consulta(visiveis, marca)
    return marca, f"nas unidades da {'Casa Lorenzi' if marca == 'lorenzi' else 'Vulto'}" if marca else "na rede inteira"


# ---------- vendas_do_periodo ----------

def vendas_do_periodo(db: Session, usuario: Usuario, p: dict, hoje: date) -> dict:
    if not usuario_tem_permissao(db, usuario, *VENDAS):
        raise SemPermissao("Sem permissão para ver vendas")
    inicio, fim = datas_do_periodo(p.get("periodo", ""), hoje, p.get("de"), p.get("ate"))
    id_unidade, canal = p.get("id_unidade"), p.get("canal")
    marca, onde = _onde(db, usuario, id_unidade, p.get("marca"))

    linhas = resumo_repository.faturamento_por_mes(
        db, inicio_dos_ultimos(1, inicio), fim_do_dia(fim), id_unidade, marca
    )
    if canal is not None:
        linhas = [linha for linha in linhas if linha["canal_venda"] == canal]

    por_canal: dict[str, dict] = defaultdict(lambda: {"pedidos": 0, "valor": ZERO})
    por_unidade: dict[str, dict] = defaultdict(lambda: {"pedidos": 0, "valor": ZERO})
    frete = ZERO
    for linha in linhas:
        valor = linha["valor"] or ZERO
        for grupo, chave in ((por_canal, linha["canal_venda"]), (por_unidade, linha["unidade"])):
            grupo[chave]["pedidos"] += linha["pedidos"]
            grupo[chave]["valor"] += valor
        frete += linha["frete"] or ZERO
    pedidos = sum(c["pedidos"] for c in por_canal.values())
    valor = sum((c["valor"] for c in por_canal.values()), ZERO)
    ticket = (valor / pedidos).quantize(Decimal("0.01")) if pedidos else None

    periodo = _descrever_periodo(inicio, fim)
    so_canal = f", só {NOME_DO_CANAL[canal]}" if canal else ""
    texto = (f"Vendas {onde} {periodo}{so_canal}: {reais(valor)} em {numero(pedidos)} pedidos"
             + (f" (ticket médio de {reais(ticket)})" if ticket else "")
             + ". Conta só vendas pagas, sem o frete, menos as devoluções; pedidos cancelados não entram.")
    return {
        "vazio": pedidos == 0,
        "periodo": {"de": data(inicio), "ate": data(fim), "dia_da_semana_de": DIAS_DA_SEMANA[inicio.weekday()],
                    "dia_da_semana_ate": DIAS_DA_SEMANA[fim.weekday()]},
        "onde": onde,
        "canal": NOME_DO_CANAL[canal] if canal else "todos",
        "faturamento": reais(valor),
        "pedidos": numero(pedidos),
        "ticket_medio": reais(ticket) if ticket else None,
        "frete_a_parte": reais(frete),
        "por_canal": [{"canal": NOME_DO_CANAL.get(c, c), "faturamento": reais(v["valor"]), "pedidos": numero(v["pedidos"])}
                      for c, v in sorted(por_canal.items())],
        "por_unidade": [{"unidade": u, "faturamento": reais(v["valor"]), "pedidos": numero(v["pedidos"])}
                        for u, v in sorted(por_unidade.items(), key=lambda item: -item[1]["valor"])],
        "regras": "Vendas pagas no período (horário de Brasília), sem o frete (à parte), menos as devoluções no dia "
                  "do estorno; pedidos cancelados não entram. É a mesma conta dos três números da Vulto.",
        "texto": texto,
        "links": [{"rotulo": "Abrir a Visão geral", "url": "/interno"}],
    }


VENDAS_DO_PERIODO = Ferramenta(
    nome="vendas_do_periodo",
    descricao=(
        "Faturamento, número de pedidos e ticket médio de um período, por canal e por unidade. Use para qualquer "
        "pergunta sobre quanto vendeu, faturou ou quantos pedidos teve. O período é um nome fixo; só use "
        "'intervalo' (com de e ate) quando a pessoa disser datas que nenhum nome cobre. O intervalo vai até 1 ano "
        "(366 dias) por consulta: um ano inteiro cabe numa consulta só. Sem unidade e sem marca, "
        "responde das marcas que a pessoa vê, com a lista por unidade: para comparar unidades, uma chamada basta."
    ),
    parametros={
        "type": "object",
        "properties": {
            "periodo": {"type": "string", "enum": list(PERIODOS),
                        "description": "Semana é de segunda a domingo; 'este_mes' e 'esta_semana' vão até hoje"},
            "de": {"type": "string", "description": "Só com periodo = intervalo. AAAA-MM-DD"},
            "ate": {"type": "string", "description": "Só com periodo = intervalo. AAAA-MM-DD, até hoje"},
            "id_unidade": {"type": "integer", "description": "Id da lista de unidades das instruções"},
            "marca": {"type": "string", "enum": list(MARCAS)},
            "canal": {"type": "string", "enum": list(CANAIS_VENDA),
                      "description": "loja = venda na loja física; site; app (só no histórico da Vulto)"},
        },
        "required": ["periodo"],
        "additionalProperties": False,
    },
    executar=vendas_do_periodo,
)

# ---------- estoque_em_um_dia ----------

# as mesmas permissões que abrem a página Estoque (case, seções 6 e 8)
VER_ESTOQUE = ("movimentar_estoque", "definir_estoque_minimo", "solicitar_transferencia", "enviar_transferencia",
               "receber_transferencia")
INICIO_DO_HISTORICO = date(2025, 1, 1)  # o histórico do estoque foi reconstruído desde essa data (ADR 0006)
PECAS_POR_RESPOSTA = 10  # mais que isso, pede para a pessoa dizer qual peça
LINHAS_DA_BUSCA = 2000


def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto.casefold()) if unicodedata.category(c) != "Mn")


# cor e tamanho como a pessoa fala: sem acento e sem o gênero ("preta" acha "Preto", "branca" acha "Branco")
def _bate(valor: str, pedido: str | None) -> bool:
    if not pedido:
        return True
    pedido = _sem_acento(pedido.strip())
    if len(pedido) > 3 and pedido[-1] in "ao":
        pedido = pedido[:-1]
    return pedido in _sem_acento(valor)


def estoque_em_um_dia(db: Session, usuario: Usuario, p: dict, hoje: date) -> dict:
    if not usuario_tem_permissao(db, usuario, *VER_ESTOQUE):
        raise SemPermissao("Sem permissão para ver o estoque")
    peca = (p.get("peca") or "").strip()
    if len(peca) == 1:
        raise RegraDeNegocio("Diga a peça: parte do nome, a etiqueta (SKU) ou o código de barras")
    texto_data = p.get("data") or hoje.isoformat()
    try:
        dia = date.fromisoformat(texto_data)
    except ValueError:
        raise RegraDeNegocio("Data inválida: use AAAA-MM-DD")
    if dia > hoje:
        raise RegraDeNegocio("A data não pode ser futura")
    if dia < INICIO_DO_HISTORICO:
        raise RegraDeNegocio(f"O histórico do estoque começa em {data(INICIO_DO_HISTORICO)}")
    id_unidade = p.get("id_unidade")
    marca, onde = _onde(db, usuario, id_unidade, p.get("marca"))

    # o dia inteiro, no horário de Brasília; hoje = o saldo de agora
    momento = fim_do_dia(dia) - timedelta(microseconds=1)
    consulta = estoque_repository.consulta_historico(momento, id_unidade=id_unidade, busca=peca or None, marca=marca)
    # sem peça é o total da unidade (ou da rede): todas as linhas; com peça, a busca tem um teto
    linhas = [l for l in db.execute(consulta if not peca else consulta.limit(LINHAS_DA_BUSCA)).mappings()
              if _bate(l["cor"], p.get("cor")) and _bate(l["tamanho"], p.get("tamanho"))]

    pecas: dict[int, dict] = {}
    for l in linhas:
        item = pecas.setdefault(l["id_variante"], {
            "produto": l["produto"], "cor": l["cor"], "tamanho": l["tamanho"], "sku": l["sku"], "unidades": {},
        })
        unidade = item["unidades"].setdefault(l["unidade"], {"loja_fisica": 0, "online": 0})
        unidade[l["canal"]] += l["quantidade"]

    quando = f"no fim de {data(dia)}" if dia < hoje else "agora"
    filtro = " ".join(x for x in (peca, p.get("cor"), p.get("tamanho")) if x)
    links = [{"rotulo": "Abrir o histórico do estoque",
              "url": f"/interno/estoque/historico?data={dia.isoformat()}&hora=23:59"
                     + (f"&unidade={id_unidade}" if id_unidade else "")}]
    regras = ("Saldo = soma das movimentações até o momento (o histórico começa em 01/01/2025); reservas de "
              "pedidos online e peças em trânsito entre lojas não entram.")
    if not peca:
        return _total_do_estoque(pecas, dia, quando, onde, filtro, regras, links)
    if not pecas:
        return {"vazio": True, "data": data(dia), "onde": onde, "busca": filtro, "regras": regras, "links": links,
                "texto": f"Não achei peça com \"{filtro}\" {onde} {quando}."}
    if len(pecas) > PECAS_POR_RESPOSTA:
        # variação = uma cor num tamanho; o total soma as peças de todas, para "quanto de camisa a gente tem?"
        produtos = sorted({f"{v['produto']} ({v['cor']})" for v in pecas.values()})
        total = sum(q["loja_fisica"] + q["online"] for v in pecas.values() for q in v["unidades"].values())
        return {"precisa_escolher": True, "variacoes_encontradas": numero(len(pecas)),
                "total_de_pecas": numero(total), "exemplos": produtos[:15], "data": data(dia), "momento": quando,
                "onde": onde, "regras": regras, "links": links,
                "texto": f"Achei {numero(len(pecas))} variações (cor e tamanho) com \"{filtro}\", somando "
                         f"{numero(total)} peças {onde} {quando}. Para ver uma, diga o nome, a cor e o tamanho, "
                         "ou a etiqueta."}

    resultado = []
    frases = []
    for item in sorted(pecas.values(), key=lambda v: (v["produto"], v["cor"], v["tamanho"])):
        unidades = [{"unidade": u, "loja_fisica": numero(q["loja_fisica"]), "online": numero(q["online"]),
                     "total": numero(q["loja_fisica"] + q["online"])} for u, q in sorted(item["unidades"].items())]
        total = sum(q["loja_fisica"] + q["online"] for q in item["unidades"].values())
        resultado.append({**{k: item[k] for k in ("produto", "cor", "tamanho", "sku")}, "total": numero(total),
                          "por_unidade": unidades})
        frases.append(f"{item['produto']} {item['cor']} {item['tamanho']}: {numero(total)} peças")
    return {
        "data": data(dia), "momento": quando, "onde": onde, "pecas": resultado, "regras": regras, "links": links,
        "texto": f"Estoque {onde} {quando}: " + "; ".join(frases) + ".",
    }


# sem peça: o total de peças (com a cor e o tamanho pedidos, se houver) por canal e por unidade
def _total_do_estoque(pecas: dict, dia: date, quando: str, onde: str, filtro: str, regras: str, links: list) -> dict:
    por_unidade: dict[str, dict] = defaultdict(lambda: {"loja_fisica": 0, "online": 0})
    for item in pecas.values():
        for unidade, q in item["unidades"].items():
            por_unidade[unidade]["loja_fisica"] += q["loja_fisica"]
            por_unidade[unidade]["online"] += q["online"]
    fisica = sum(q["loja_fisica"] for q in por_unidade.values())
    online = sum(q["online"] for q in por_unidade.values())
    com_saldo = sum(1 for item in pecas.values() if sum(q["loja_fisica"] + q["online"]
                                                        for q in item["unidades"].values()) > 0)
    so = f" ({filtro})" if filtro else ""
    return {
        "vazio": not pecas, "data": data(dia), "momento": quando, "onde": onde, "filtro": filtro or None,
        "total_de_pecas": numero(fisica + online), "loja_fisica": numero(fisica), "online": numero(online),
        "variacoes_com_saldo": numero(com_saldo),
        "por_unidade": [{"unidade": u, "loja_fisica": numero(q["loja_fisica"]), "online": numero(q["online"]),
                         "total": numero(q["loja_fisica"] + q["online"])}
                        for u, q in sorted(por_unidade.items(), key=lambda x: -(x[1]["loja_fisica"] + x[1]["online"]))],
        "regras": regras, "links": links,
        "texto": f"Estoque{so} {onde} {quando}: {numero(fisica + online)} peças ({numero(fisica)} na loja física e "
                 f"{numero(online)} no online), em {numero(com_saldo)} variações com saldo.",
    }


ESTOQUE_EM_UM_DIA = Ferramenta(
    nome="estoque_em_um_dia",
    descricao=(
        "Quantas peças havia (ou há agora), por unidade e canal (loja física e online). Com peça: a peça é "
        "buscada por parte do nome, pela etiqueta (SKU) ou pelo código de barras, e cor e tamanho afinam a busca; "
        "se vierem muitas variações (cor e tamanho), a resposta traz o total somado e pede para a pessoa dizer "
        "qual. Sem peça: o total de peças da unidade, da marca ou da rede (ex.: 'quantas peças tinha na loja'). "
        "Sem data, é o saldo de agora. Datas desde 01/01/2025."
    ),
    parametros={
        "type": "object",
        "properties": {
            "peca": {"type": "string", "description": "Parte do nome do produto, a etiqueta (SKU) ou o código de "
                                                      "barras. Vazio: o total de todas as peças"},
            "cor": {"type": "string"},
            "tamanho": {"type": "string", "description": "Ex.: P, M, G, 38"},
            "data": {"type": "string", "description": "AAAA-MM-DD; vazio = agora. O saldo vale no fim desse dia"},
            "id_unidade": {"type": "integer", "description": "Id da lista de unidades das instruções"},
            "marca": {"type": "string", "enum": list(MARCAS)},
        },
        "additionalProperties": False,
    },
    executar=estoque_em_um_dia,
)

# ---------- pecas_paradas ----------

DIAS_PARADA_SEM_HISTORICO = 60  # categoria com pouca venda no último ano para medir o normal dela
DIAS_PARADA_MINIMO = 15
DIAS_PARADA_MAXIMO = 180
INTERVALOS_PARA_MEDIR = 30  # com menos intervalos entre vendas, o "normal" da categoria não é confiável
HISTORICO_DO_NORMAL = 365  # dias de vendas usados para medir o normal de cada categoria
PARADAS_NA_RESPOSTA = 15


# o prazo de cada categoria: sem venda há mais tempo do que 9 de cada 10 intervalos entre vendas dessa categoria
# no último ano (entre DIAS_PARADA_MINIMO e DIAS_PARADA_MAXIMO). Categoria com pouca venda usa o padrão fixo
def prazos_das_categorias(db: Session, hoje: date) -> dict[int, tuple[str, int]]:
    medidos = estoque_repository.prazos_por_categoria(db, inicio_dos_ultimos(HISTORICO_DO_NORMAL, hoje))
    return {
        id_categoria: (nome, min(DIAS_PARADA_MAXIMO, max(DIAS_PARADA_MINIMO, round(dias)))
                       if intervalos >= INTERVALOS_PARA_MEDIR else DIAS_PARADA_SEM_HISTORICO)
        for id_categoria, (nome, dias, intervalos) in medidos.items()
    }


def pecas_paradas(db: Session, usuario: Usuario, p: dict, hoje: date) -> dict:
    if not usuario_tem_permissao(db, usuario, *VER_ESTOQUE):
        raise SemPermissao("Sem permissão para ver o estoque")
    dias = p.get("dias")
    if dias is not None and not DIAS_PARADA_MINIMO <= dias <= PERIODO_MAXIMO:
        raise RegraDeNegocio(f"Use de {DIAS_PARADA_MINIMO} a {PERIODO_MAXIMO} dias")
    id_unidade = p.get("id_unidade")
    marca, onde = _onde(db, usuario, id_unidade, p.get("marca"))

    # sem venda desde antes do começo do dia de corte (horário de Brasília). Sem prazo na pergunta, cada categoria
    # tem o seu (o normal dela); com prazo, vale o mesmo para todas
    def corte(prazo: int):
        return inicio_dos_ultimos(1, hoje - timedelta(days=prazo))

    prazos = {} if dias is not None else prazos_das_categorias(db, hoje)
    consulta = estoque_repository.consulta_pecas_paradas(
        corte(dias if dias is not None else DIAS_PARADA_SEM_HISTORICO), id_unidade=id_unidade, marca=marca,
        cortes_por_categoria={c: corte(d) for c, (_, d) in prazos.items()} or None,
    )
    linhas = list(db.execute(consulta.limit(PARADAS_NA_RESPOSTA)).mappings())
    links = [{"rotulo": "Abrir o estoque", "url": "/interno/estoque"}]
    if dias is not None:
        regra = f"há mais de {dias} dias"
        regras = (f"Parada = com saldo hoje e sem venda naquela unidade há mais de {dias} dias (desde antes de "
                  f"{data(corte(dias).date())}), o prazo pedido, igual para todas as categorias.")
    else:
        regra = "há mais tempo que o normal da categoria"
        regras = ("Parada = com saldo hoje e sem venda naquela unidade há mais tempo que o normal da categoria: o "
                  "tempo em que 9 de cada 10 peças dessa categoria vendem de novo, medido nas vendas das lojas no "
                  f"último ano (entre {DIAS_PARADA_MINIMO} e {DIAS_PARADA_MAXIMO} dias; categoria com pouca venda "
                  f"usa {DIAS_PARADA_SEM_HISTORICO}). A pessoa pode pedir um prazo único.")
    regras += (" Se a peça nunca vendeu na unidade, conta desde que chegou nela; o histórico começa em 01/01/2025. "
               "Cada linha é uma cor num tamanho numa unidade.")
    base = {"regra": regra, "onde": onde, "regras": regras, "links": links,
            "prazos_por_categoria": [{"categoria": nome, "dias": numero(d)}
                                     for nome, d in sorted(prazos.values(), key=lambda x: (x[1], x[0]))]}
    if dias is not None:
        base["dias"] = numero(dias)
    if not linhas:
        return {**base, "vazio": True, "texto": f"Nenhuma peça parada ({regra}) {onde}."}

    total_linhas, total_pecas = linhas[0]["total_linhas"], int(linhas[0]["total_pecas"])
    pecas = [{
        "produto": l["produto"], "cor": l["cor"], "tamanho": l["tamanho"], "sku": l["sku"], "unidade": l["unidade"],
        "quantidade": numero(int(l["quantidade"])),
        "ultima_venda": data(l["ultima_venda"].astimezone(BRASILIA).date()) if l["ultima_venda"] else
        "nunca vendeu nessa unidade",
        "dias_parada": numero((hoje - l["parada_desde"].astimezone(BRASILIA).date()).days),
        **({"normal_da_categoria": f"{numero(prazos[l['id_categoria']][1])} dias ({prazos[l['id_categoria']][0]})"}
           if l["id_categoria"] in prazos else {}),
    } for l in linhas]
    return {
        **base, "linhas_paradas": numero(total_linhas), "pecas_paradas": numero(total_pecas),
        "mostrando": numero(len(pecas)), "mais_antigas": pecas,
        "texto": f"{numero(total_linhas)} variações paradas ({regra}) {onde}, somando {numero(total_pecas)} peças. "
                 f"A mais antiga: {pecas[0]['produto']} {pecas[0]['cor']} {pecas[0]['tamanho']} na "
                 f"{pecas[0]['unidade']}, {pecas[0]['dias_parada']} dias sem venda.",
    }


PECAS_PARADAS = Ferramenta(
    nome="pecas_paradas",
    descricao=(
        "Peças paradas: com saldo hoje e sem venda naquela unidade há mais tempo que o normal da categoria (o "
        "tempo em que 9 de cada 10 peças dela vendem de novo, medido no último ano; acessórios vendem rápido, "
        "jaquetas devagar). Com 'dias', usa esse prazo para todas. Traz quantas variações (cor e tamanho por "
        "unidade) e peças estão paradas, o prazo de cada categoria e as mais antigas. Use para 'o que está "
        "parado', 'o que não vende', 'estoque encalhado'."
    ),
    parametros={
        "type": "object",
        "properties": {
            "dias": {"type": "integer", "description": f"Só se a pessoa pedir um prazo; de {DIAS_PARADA_MINIMO} a "
                                                       f"{PERIODO_MAXIMO}. Vazio: o normal de cada categoria"},
            "id_unidade": {"type": "integer", "description": "Id da lista de unidades das instruções"},
            "marca": {"type": "string", "enum": list(MARCAS)},
        },
        "additionalProperties": False,
    },
    executar=pecas_paradas,
)

# ---------- pontos_vencendo ----------

DIAS_VENCENDO_PADRAO = 30


def _nome_da_marca(marca: str) -> str:
    return "Casa Lorenzi" if marca == "lorenzi" else "Vulto"


# de quando são os pontos de um grupo: o lote mais antigo e o mais recente (dia de Brasília)
def _ganhos_entre(linha) -> str:
    desde, ate = (linha[c].astimezone(BRASILIA).date() for c in ("ganho_desde", "ganho_ate"))
    return f"em {data(desde)}" if desde == ate else f"de {data(desde)} a {data(ate)}"


def pontos_vencendo(db: Session, usuario: Usuario, p: dict, hoje: date) -> dict:
    if not usuario_tem_permissao(db, usuario, "gerenciar_fidelidade"):
        raise SemPermissao("Sem permissão para ver os pontos")
    dias = DIAS_VENCENDO_PADRAO if p.get("dias") is None else p["dias"]
    if not 1 <= dias <= PERIODO_MAXIMO:
        raise RegraDeNegocio(f"Use de 1 a {PERIODO_MAXIMO} dias")
    marca = marca_da_consulta(marcas_visiveis(db, usuario), p.get("marca"))
    ate_dia = hoje + timedelta(days=dias)
    agora, ate = datetime.now(timezone.utc), fim_do_dia(ate_dia)

    por_marca = fidelidade_repository.pontos_vencendo(db, agora, ate, marca)
    proximos = fidelidade_repository.proximo_vencimento(db, agora, marca)
    onde = f"na {_nome_da_marca(marca)}" if marca else "nas marcas que você vê"
    regras = ("Pontos que sobram nos lotes e vencem no prazo (cada ponto vale o preço da época em que foi ganho). "
              "Cada lote vence 12 meses depois de ganho (18 no Ouro); como a validade só passou a valer em "
              "01/10/2026, os lotes ganhos antes disso vencem em 01/10/2027. Lote sem data ainda não tem "
              "vencimento. \"ganhos_entre\" é o lote mais antigo e o mais recente do grupo. Pontos de cada marca "
              "separados, nunca "
              "somados.")
    proximo = [{"marca": _nome_da_marca(l["marca"]), "dia": data(l["dia"]), "pontos": numero(int(l["pontos"])),
                "ganhos_entre": _ganhos_entre(l)} for l in proximos]
    base = {"prazo": f"até {data(ate_dia)} ({dias} dias)", "onde": onde, "proximo_vencimento": proximo,
            "regras": regras, "links": [{"rotulo": "Abrir a fidelidade", "url": "/interno/fidelidade"}]}
    if not por_marca:
        depois = "; ".join(f"na {x['marca']}, {x['dia']} ({x['pontos']} pontos)" for x in proximo)
        return {**base, "vazio": True,
                "texto": f"Nenhum ponto vence até {data(ate_dia)} {onde}."
                         + (f" O próximo vencimento: {depois}." if depois else " Não há pontos com vencimento.")}
    marcas = [{
        "marca": _nome_da_marca(l["marca"]), "clientes": numero(l["clientes"]), "pontos": numero(int(l["pontos"])),
        "valor": reais(Decimal(l["reais"]).quantize(Decimal("0.01"))),
        "primeiro_vencimento": data(l["primeiro"].astimezone(BRASILIA).date()),
        "ganhos_entre": _ganhos_entre(l),
    } for l in por_marca]
    return {**base, "por_marca": marcas,
            "texto": f"Pontos vencendo até {data(ate_dia)}: " + "; ".join(
                f"na {x['marca']}, {x['pontos']} pontos ({x['valor']}) de {x['clientes']} clientes, o primeiro em "
                f"{x['primeiro_vencimento']}" for x in marcas) + "."}


PONTOS_VENCENDO = Ferramenta(
    nome="pontos_vencendo",
    descricao=(
        "Pontos de fidelidade que vencem nos próximos X dias (padrão 30), por marca: quantos clientes, quantos "
        "pontos, quanto valem e de quando são (as datas em que foram ganhos), e o próximo vencimento quando não "
        "há nada no prazo. Use também para 'quando esses pontos foram ganhos'. Pontos de cada marca nunca se somam."
    ),
    parametros={
        "type": "object",
        "properties": {
            "dias": {"type": "integer", "description": f"Prazo a partir de hoje; padrão {DIAS_VENCENDO_PADRAO}, "
                                                       f"até {PERIODO_MAXIMO}"},
            "marca": {"type": "string", "enum": list(MARCAS)},
        },
        "additionalProperties": False,
    },
    executar=pontos_vencendo,
)

# ---------- desempenho_equipe ----------

PESSOAS_NA_RESPOSTA = 10


def _uma_casa(valor) -> str | None:
    return None if valor is None else f"{Decimal(str(valor)).quantize(Decimal('0.1'), ROUND_HALF_UP)}".replace(".", ",")


# desempenho de pessoas com nome: só o Admin, como o faturamento por unidade e a rede agora da Visão geral
def desempenho_equipe(db: Session, usuario: Usuario, p: dict, hoje: date) -> dict:
    if usuario.tipo_conta != "interna" or not permissao_repository.modelo_eh_admin(db, usuario.id_modelo_acesso):
        raise SemPermissao("Só o Admin vê o desempenho da equipe")
    inicio, fim = datas_do_periodo(p.get("periodo", ""), hoje, p.get("de"), p.get("ate"))
    id_unidade, area = p.get("id_unidade"), p.get("area")
    marca, onde = _onde(db, usuario, id_unidade, p.get("marca"))
    desde, ate = inicio_dos_ultimos(1, inicio), fim_do_dia(fim)
    periodo = _descrever_periodo(inicio, fim)

    resultado = {"periodo": {"de": data(inicio), "ate": data(fim)}, "onde": onde,
                 "links": [{"rotulo": "Abrir a Visão geral", "url": "/interno"}]}
    regras, frases = [], []
    if area in (None, "vendas"):
        vendas = resumo_repository.vendas_por_vendedor(db, desde, ate, id_unidade, marca)
        resultado["vendedores"] = numero(len(vendas))
        resultado["vendas_por_vendedor"] = [{
            "vendedor": v["vendedor"], "pedidos": numero(v["pedidos"]), "faturamento": reais(v["valor"] or ZERO),
            "ticket_medio": reais((v["valor"] / v["pedidos"]).quantize(Decimal("0.01"))) if v["pedidos"] else None,
        } for v in vendas[:PESSOAS_NA_RESPOSTA]]
        regras.append("Vendas: só a loja física, que registra quem vendeu (site e app não têm vendedor); pedido "
                      "pago no período, sem cancelados e sem o frete; aqui as devoluções não são descontadas, então "
                      "o total pode ser maior que o de vendas_do_periodo.")
        if vendas:
            v = resultado["vendas_por_vendedor"][0]
            frases.append(f"quem mais vendeu na loja física foi {v['vendedor']}, com {v['faturamento']} em "
                          f"{v['pedidos']} pedidos")
    if area in (None, "atendimento"):
        chamados = resumo_repository.chamados_por_atendente(db, desde, ate, id_unidade, marca)
        resultado["atendentes"] = numero(len(chamados))
        resultado["chamados_por_atendente"] = [{
            "atendente": c["atendente"], "concluidos": numero(c["concluidos"]),
            "mediana_horas_ate_concluir": _uma_casa(c["mediana_horas"]),
        } for c in chamados[:PESSOAS_NA_RESPOSTA]]
        regras.append("Atendimento: chamados concluídos no período pelo responsável; a mediana é o tempo da "
                      "abertura até a conclusão. Chamados migrados da Vulto não guardam quando foram concluídos: "
                      "contam nos concluídos, mas não no tempo (vazio quando só há migrados). Chamados sem "
                      "responsável não entram.")
        if chamados:
            c = resultado["chamados_por_atendente"][0]
            frases.append(f"quem mais concluiu chamados foi {c['atendente']}, com {c['concluidos']}")
    resultado["regras"] = " ".join(regras)
    resultado["vazio"] = not frases
    resultado["texto"] = (f"Desempenho da equipe {onde} {periodo}: " + "; ".join(frases) + "." if frases
                          else f"Nenhuma venda na loja física nem chamado concluído {onde} {periodo}.")
    return resultado


DESEMPENHO_EQUIPE = Ferramenta(
    nome="desempenho_equipe",
    descricao=(
        "Desempenho de cada pessoa da equipe num período: vendas na loja física por vendedor (faturamento, "
        "pedidos, ticket) e chamados concluídos por atendente (quantos e o tempo mediano até concluir). Só o "
        "Admin vê. Use para 'quem vendeu mais', 'melhor vendedor', 'quem atendeu mais chamados'. O período segue "
        "os mesmos nomes de vendas_do_periodo."
    ),
    parametros={
        "type": "object",
        "properties": {
            "periodo": {"type": "string", "enum": list(PERIODOS)},
            "de": {"type": "string", "description": "Só com periodo = intervalo. AAAA-MM-DD"},
            "ate": {"type": "string", "description": "Só com periodo = intervalo. AAAA-MM-DD, até hoje"},
            "area": {"type": "string", "enum": ["vendas", "atendimento"], "description": "Vazio: as duas"},
            "id_unidade": {"type": "integer", "description": "Id da lista de unidades das instruções"},
            "marca": {"type": "string", "enum": list(MARCAS)},
        },
        "required": ["periodo"],
        "additionalProperties": False,
    },
    executar=desempenho_equipe,
)

# ---------- metricas_assistente ----------

CONVERSA_EM_ANDAMENTO = timedelta(hours=1)  # a linha nasce no começo da conversa: antes disso, sem resultado
NOME_DO_ASSUNTO = {"pecas": "peças", "carrinho": "link de compra", "estoque": "estoque", "lojas": "lojas e horário",
                   "pontos": "pontos", "pedidos": "pedidos", "chamado": "chamado"}


def _pct(parte: int, todo: int) -> str | None:
    return None if not todo else f"{Decimal(parte * 100 / todo).quantize(Decimal('1'), ROUND_HALF_UP)}%"


# o resumo anônimo das conversas do assistente do cliente (tabela da Nicole, só leitura). Ele não guarda a marca:
# só quem vê as duas marcas consulta, e a resposta é da rede inteira
def metricas_assistente(db: Session, usuario: Usuario, p: dict, hoje: date) -> dict:
    eh_admin = usuario.tipo_conta == "interna" and permissao_repository.modelo_eh_admin(db, usuario.id_modelo_acesso)
    if not eh_admin and not usuario_tem_permissao(db, usuario, "atender_chamado"):
        raise SemPermissao("Sem permissão para ver as métricas do atendimento")
    if marcas_visiveis(db, usuario) != set(MARCAS):
        raise SemPermissao("As métricas do assistente do cliente são da rede inteira: só quem vê as duas marcas")
    # sem período na pergunta: os últimos 30 dias (a resposta diz o período usado)
    inicio, fim = datas_do_periodo(p.get("periodo") or "ultimos_30_dias", hoje, p.get("de"), p.get("ate"))
    canal = p.get("canal")
    agora = datetime.now(timezone.utc)
    n = assistente_interno_repository.resumo_das_conversas(
        db, inicio_dos_ultimos(1, inicio), fim_do_dia(fim), agora - CONVERSA_EM_ANDAMENTO, canal
    )
    periodo = _descrever_periodo(inicio, fim)
    onde = f"no {'WhatsApp' if canal == 'whatsapp' else 'chat'}" if canal else "no WhatsApp e no chat"
    regras = ("Conversas do assistente do cliente, anônimas, da rede inteira (o resumo não guarda a marca). Entram "
              "só as com algum assunto além de fora do tema e que começaram há mais de 1 hora. Cada conversa cai "
              "num grupo só: erro do sistema (uma ferramenta falhou), virou chamado, avaliação negativa ou "
              "resolvida. Avaliação é opcional: a porcentagem positiva conta só as avaliadas. \"Se identificaram\" "
              "= confirmaram CPF e código (não quer dizer que os outros não têm conta).")
    base = {"periodo": {"de": data(inicio), "ate": data(fim)}, "onde": onde, "regras": regras,
            "so_fora_do_tema": numero(n["so_fora_do_tema"]), "sem_assunto": numero(n["sem_assunto"]),
            "links": [{"rotulo": "Abrir o atendimento", "url": "/interno/atendimento"}]}
    total = n["conversas"]
    if not total:
        return {**base, "vazio": True,
                "texto": f"Nenhuma conversa do assistente do cliente {onde} {periodo}."}
    grupos = {"resolvidas": n["resolvidas"], "viraram_chamado": n["viraram_chamado"],
              "avaliacao_negativa": n["negativas"], "erro_do_sistema": n["erro_do_sistema"]}
    return {
        **base, "conversas": numero(total),
        "grupos": {k: numero(v) for k, v in grupos.items()},
        "taxa_resolvidas": _pct(n["resolvidas"], total),
        "se_identificaram": numero(n["se_identificaram"]), "taxa_se_identificaram": _pct(n["se_identificaram"], total),
        "avaliadas": numero(n["avaliadas"]), "positivas_entre_avaliadas": _pct(n["positivas"], n["avaliadas"]),
        "por_assunto": [{"assunto": NOME_DO_ASSUNTO.get(a["assunto"], a["assunto"]), "conversas": numero(a["conversas"])}
                        for a in n["por_assunto"]],
        "texto": f"Assistente do cliente {onde} {periodo}: {numero(total)} conversas, {numero(n['resolvidas'])} "
                 f"resolvidas, {numero(n['viraram_chamado'])} viraram chamado, {numero(n['negativas'])} com 👎 e "
                 f"{numero(n['erro_do_sistema'])} com erro do sistema.",
    }


METRICAS_ASSISTENTE = Ferramenta(
    nome="metricas_assistente",
    descricao=(
        "Como foi o assistente do cliente (WhatsApp e chat) num período: quantas conversas, quantas resolveu "
        "sozinho, quantas viraram chamado, avaliações, erros do sistema, quantos clientes se identificaram e os "
        "assuntos mais perguntados. Anônimo e da rede inteira. O período segue os nomes de vendas_do_periodo; "
        "sem período, os últimos 30 dias."
    ),
    parametros={
        "type": "object",
        "properties": {
            "periodo": {"type": "string", "enum": list(PERIODOS), "description": "Padrão: ultimos_30_dias"},
            "de": {"type": "string", "description": "Só com periodo = intervalo. AAAA-MM-DD"},
            "ate": {"type": "string", "description": "Só com periodo = intervalo. AAAA-MM-DD, até hoje"},
            "canal": {"type": "string", "enum": ["whatsapp", "chat"], "description": "Vazio: os dois"},
        },
        "additionalProperties": False,
    },
    executar=metricas_assistente,
)

# ---------- anomalias ----------

def _dia(momento) -> str:
    return data(momento.astimezone(BRASILIA).date())


# o que merece um olhar de uma pessoa: não diz que há erro, só que foge do normal. Só o Admin, como o desempenho
def anomalias(db: Session, usuario: Usuario, p: dict, hoje: date) -> dict:
    if usuario.tipo_conta != "interna" or not permissao_repository.modelo_eh_admin(db, usuario.id_modelo_acesso):
        raise SemPermissao("Só o Admin vê as anomalias")
    inicio, fim = datas_do_periodo(p.get("periodo") or "ultimos_30_dias", hoje, p.get("de"), p.get("ate"))
    id_unidade = p.get("id_unidade")
    marca, onde = _onde(db, usuario, id_unidade, p.get("marca"))
    desde, ate = inicio_dos_ultimos(1, inicio), fim_do_dia(fim)
    r = resumo_repository

    precos = r.precos_fora_do_catalogo(db, desde, ate, id_unidade, marca)
    valores = r.vendas_fora_do_padrao(db, desde, ate, id_unidade, marca)
    repetidos = r.pedidos_repetidos(db, desde, ate, id_unidade, marca)
    negativos = r.estoque_negativo(db, id_unidade, marca)
    total = lambda linhas: linhas[0]["total"] if linhas else 0  # noqa: E731
    verificacoes = {
        "preco_fora_do_catalogo": {
            "itens": numero(total(precos)),
            "exemplos": [{"venda": l["codigo_venda"], "dia": _dia(l["pago_em"]), "unidade": l["unidade"],
                          "peca": f"{l['produto']} ({l['sku']})", "vendido_por": reais(l["preco_unitario"]),
                          "catalogo_na_epoca": reais(l["preco_catalogo"])} for l in precos],
        },
        "venda_fora_do_padrao": {
            "pedidos": numero(total(valores)),
            "exemplos": [{"venda": l["codigo_venda"], "dia": _dia(l["pago_em"]), "unidade": l["unidade"],
                          "canal": NOME_DO_CANAL.get(l["canal_venda"], l["canal_venda"]), "valor": reais(l["valor"]),
                          "mediana_do_canal": reais(Decimal(l["mediana"]).quantize(Decimal("0.01")))}
                         for l in valores],
        },
        "pedido_repetido": {
            "pares": numero(total(repetidos)),
            "exemplos": [{"venda": l["codigo_venda"], "repetida_em": l["repetido"], "dia": _dia(l["pago_em"]),
                          "unidade": l["unidade"], "valor": reais(l["valor_total"])} for l in repetidos],
        },
        "estoque_negativo": {
            "linhas": numero(total(negativos)),
            "exemplos": [{"peca": f"{l['produto']} {l['cor']} {l['tamanho']} ({l['sku']})", "unidade": l["unidade"],
                          "canal": "loja física" if l["canal"] == "loja_fisica" else "online",
                          "saldo": numero(l["quantidade"])} for l in negativos],
        },
    }
    def contagem(n: int, um: str, varios: str) -> str:
        return f"{numero(n)} {um if n == 1 else varios}"

    achados = [contagem(total(precos), "item vendido longe do preço do catálogo",
                        "itens vendidos longe do preço do catálogo"),
               contagem(total(valores), "venda muito acima do normal do canal", "vendas muito acima do normal do canal"),
               contagem(total(repetidos), "par de pedidos repetidos", "pares de pedidos repetidos"),
               contagem(total(negativos), "saldo de estoque negativo", "saldos de estoque negativos") + " (agora)"]
    periodo = _descrever_periodo(inicio, fim)
    return {
        "vazio": not any((precos, valores, repetidos, negativos)),
        "periodo": {"de": data(inicio), "ate": data(fim)}, "onde": onde, "verificacoes": verificacoes,
        "regras": (f"Preço fora do catálogo: item vendido mais de {int(r.PRECO_FORA_DO_CATALOGO * 100)}% acima ou "
                   "abaixo do preço que valia na data da venda (sem preço de referência, como a peça 'sem cadastro' "
                   f"da migração, fica de fora). Venda fora do padrão: pedido acima de {r.VEZES_A_MEDIANA} vezes a "
                   "mediana do mesmo canal no período, sem o frete (só com pelo menos "
                   f"{r.PEDIDOS_PARA_MEDIANA} pedidos no canal). Pedido repetido: mesmo cliente ou CPF da nota, "
                   f"mesma unidade e mesmo valor em até {r.REPETIDO_EM // 60} minutos. Estoque negativo: agora, "
                   "sem período. Anomalia é o que foge do normal e merece um olhar, não prova de erro. Até "
                   f"{r.EXEMPLOS_DE_ANOMALIA} exemplos por verificação."),
        "links": [{"rotulo": "Abrir os pedidos", "url": "/interno/pedidos"}],
        "texto": f"Anomalias {onde} {periodo}: " + "; ".join(achados) + ".",
    }


ANOMALIAS = Ferramenta(
    nome="anomalias",
    descricao=(
        "O que foge do normal num período e merece um olhar: itens vendidos longe do preço do catálogo da época, "
        "vendas muito acima do normal do canal, pedidos que parecem lançados duas vezes e estoque negativo. Traz a "
        "contagem e exemplos com o código da venda. Só o Admin vê. Sem período, os últimos 30 dias."
    ),
    parametros={
        "type": "object",
        "properties": {
            "periodo": {"type": "string", "enum": list(PERIODOS), "description": "Padrão: ultimos_30_dias"},
            "de": {"type": "string", "description": "Só com periodo = intervalo. AAAA-MM-DD"},
            "ate": {"type": "string", "description": "Só com periodo = intervalo. AAAA-MM-DD, até hoje"},
            "id_unidade": {"type": "integer", "description": "Id da lista de unidades das instruções"},
            "marca": {"type": "string", "enum": list(MARCAS)},
        },
        "additionalProperties": False,
    },
    executar=anomalias,
)

FERRAMENTAS = {f.nome: f for f in (VENDAS_DO_PERIODO, ESTOQUE_EM_UM_DIA, PECAS_PARADAS, PONTOS_VENCENDO,
                                   DESEMPENHO_EQUIPE, METRICAS_ASSISTENTE, ANOMALIAS)}
