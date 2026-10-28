import calendar
import json
import re
import unicodedata
from datetime import date, timedelta

# conferência sem IA (trava da CFO): todo número do texto do modelo tem que estar no que as ferramentas devolveram,
# na pergunta ou nas instruções (a data de hoje, os ids das unidades). "R$ 48.230,00" vira "48.230,00".
# Datas também são conferidas: uma data só passa se veio desses mesmos lugares, se é ontem, hoje ou amanhã, ou se
# é o primeiro ou o último dia de um mês ou ano que a pessoa citou ("setembro de 2025", "em 2025") ou de um período
# relativo citado ("do mês", "semana passada", "este ano": o atual e o anterior). Assim o modelo
# não calcula uma data por conta própria (ex.: "vence em 01/10/2027, então foi ganho em 01/10/2026").
# Um dígito sozinho passa ("as 2 lojas", "1 pedido"): erra pouco e travaria quase toda resposta
NUMERO = re.compile(r"\d+(?:[.,]\d+)*")
# dd/mm/aaaa ou só dd/mm ("de 01/01 a 30/06")
DATA = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{4}))?\b")
ANO = re.compile(r"\b(20\d{2})\b")
MESES = ("janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
         "novembro", "dezembro")


def _data(dia: int, mes: int, ano: int) -> date | None:
    try:
        return date(ano, mes, dia)
    except ValueError:
        return None


def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto.casefold()) if unicodedata.category(c) != "Mn")


# as datas escritas num texto (dd/mm/aaaa); as sem ano ficam de fora, porque não dizem o dia certo
def _datas_do_texto(texto: str) -> set[date]:
    return {d for dia, mes, ano in DATA.findall(texto) if ano and (d := _data(int(dia), int(mes), int(ano)))}


# o que a pessoa pode ouvir sem estar em lugar nenhum: ontem, hoje, amanhã e as pontas dos anos e meses citados
def _datas_livres(pergunta: str, hoje: date | None) -> set[date]:
    livres: set[date] = set()
    if hoje is not None:
        livres |= {hoje - timedelta(days=1), hoje, hoje + timedelta(days=1)}
    anos = {int(a) for a in ANO.findall(pergunta)}
    for ano in anos:
        livres |= {date(ano, 1, 1), date(ano, 12, 31)}
    texto = _sem_acento(pergunta)
    # períodos relativos citados ("do mês", "semana passada", "este ano"): as pontas que o backend calcularia
    if hoje is not None:
        if re.search(r"\bmes\b", texto):
            primeiro = hoje.replace(day=1)
            anterior = (primeiro - timedelta(days=1)).replace(day=1)
            livres |= {primeiro, date(hoje.year, hoje.month, calendar.monthrange(hoje.year, hoje.month)[1]),
                       anterior, primeiro - timedelta(days=1)}
        if re.search(r"\bsemanas?\b", texto):
            segunda = hoje - timedelta(days=hoje.weekday())
            livres |= {segunda, segunda + timedelta(days=6), segunda - timedelta(days=7), segunda - timedelta(days=1)}
        if re.search(r"\banos?\b", texto):
            livres |= {date(hoje.year, 1, 1), date(hoje.year, 12, 31), date(hoje.year - 1, 1, 1),
                       date(hoje.year - 1, 12, 31)}
    anos_do_mes = anos or ({hoje.year} if hoje else set())
    for numero_do_mes, mes in enumerate(MESES, start=1):
        if re.search(rf"\b{mes}\b", texto):
            for ano in anos_do_mes:
                ultimo = calendar.monthrange(ano, numero_do_mes)[1]
                livres |= {date(ano, numero_do_mes, 1), date(ano, numero_do_mes, ultimo)}
    return livres


def numeros(texto: str) -> set[str]:
    return set(NUMERO.findall(texto))


# os números e as datas do texto que não vieram de lugar nenhum (vazio = passou)
def numeros_sem_origem(texto: str, resultados: list[dict], pergunta: str, instrucoes: str = "",
                       hoje: date | None = None) -> set[str]:
    fontes = "\n".join([pergunta, instrucoes, *(json.dumps(r, ensure_ascii=False, default=str) for r in resultados)])
    permitidos = numeros(fontes)
    datas = _datas_do_texto(fontes) | _datas_livres(pergunta, hoje)
    dias_e_meses = {(d.day, d.month) for d in datas}
    sem_origem: set[str] = set()

    def conferir_data(trecho: re.Match) -> str:
        dia, mes, ano = int(trecho.group(1)), int(trecho.group(2)), trecho.group(3)
        if _data(dia, mes, int(ano or 2000)) is None:
            return trecho.group(0)  # não é data (ex.: 45/60): os números são conferidos um a um
        if (ano and date(int(ano), mes, dia) not in datas) or (not ano and (dia, mes) not in dias_e_meses):
            sem_origem.add(trecho.group(0))
        return " "

    resto = DATA.sub(conferir_data, texto)
    return sem_origem | {n for n in numeros(resto) if len(n) > 1 and n not in permitidos}


def numeros_conferem(texto: str, resultados: list[dict], pergunta: str, instrucoes: str = "",
                     hoje: date | None = None) -> bool:
    return not numeros_sem_origem(texto, resultados, pergunta, instrucoes, hoje)
