import json
import re
from datetime import date

# conferência sem IA (trava da CFO): todo número do texto do modelo tem que estar no que as ferramentas devolveram,
# na pergunta ou nas instruções (a data de hoje, os ids das unidades). "R$ 48.230,00" vira "48.230,00".
# Duas exceções, que não são número do negócio: uma data válida (ex.: "amanhã, 29/10/2026", "até 30/06") e um
# dígito sozinho ("as 2 lojas", "1 pedido"), que erra pouco e travaria quase toda resposta
NUMERO = re.compile(r"\d+(?:[.,]\d+)*")
# dd/mm/aaaa ou só dd/mm ("de 01/01 a 30/06"); sem o ano, vale qualquer dia que exista (29/02 também)
DATA = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{4}))?\b")


def _data_valida(trecho: re.Match) -> bool:
    dia, mes = int(trecho.group(1)), int(trecho.group(2))
    ano = int(trecho.group(3) or 2000)
    try:
        date(ano, mes, dia)
    except ValueError:
        return False
    return True


def _sem_datas(texto: str) -> str:
    return DATA.sub(lambda trecho: " " if _data_valida(trecho) else trecho.group(0), texto)


def numeros(texto: str) -> set[str]:
    return set(NUMERO.findall(texto))


# os números do texto que não vieram de lugar nenhum (vazio = passou)
def numeros_sem_origem(texto: str, resultados: list[dict], pergunta: str, instrucoes: str = "") -> set[str]:
    permitidos = numeros(pergunta) | numeros(instrucoes)
    for resultado in resultados:
        permitidos |= numeros(json.dumps(resultado, ensure_ascii=False, default=str))
    return {n for n in numeros(_sem_datas(texto)) if len(n) > 1 and n not in permitidos}


def numeros_conferem(texto: str, resultados: list[dict], pergunta: str, instrucoes: str = "") -> bool:
    return not numeros_sem_origem(texto, resultados, pergunta, instrucoes)
