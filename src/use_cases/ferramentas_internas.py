from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Callable, get_args

from sqlalchemy.orm import Session

from src.entities.comum import Marca
from src.models.contas import Usuario
from src.repositories import resumo_repository, unidade_repository
from src.use_cases.erros import RecursoNaoEncontrado, RegraDeNegocio, SemPermissao
from src.use_cases.permissoes import conferir_marca, marca_da_consulta, marcas_visiveis, usuario_tem_permissao
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

FERRAMENTAS = {f.nome: f for f in (VENDAS_DO_PERIODO,)}
