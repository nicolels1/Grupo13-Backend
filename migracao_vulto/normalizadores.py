# Funções puras de padronização dos dados da Vulto. Só as transformações automáticas: formato
# de data, número, CPF, e-mail, telefone, texto e encoding — nada que dependa de regra de negócio.
# Cada função recebe o valor cru como veio do SQLite, da planilha ou do CSV e devolve o valor
# padronizado, ou None quando a célula está vazia. Quando o valor existe mas não segue nenhum
# formato conhecido, a função levanta ValorInvalido e quem chamou registra a pendência:
# limpar não é inventar, então nada é "consertado" no chute.
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from src.utils.cpf import cpf_valido, normalizar_cpf

BRASILIA = ZoneInfo("America/Sao_Paulo")
CENTAVO = Decimal("0.01")


class ValorInvalido(ValueError):
    """Valor presente que não segue nenhum formato conhecido; vira pendência, não é corrigido."""


# ---------- vazio e texto ----------

# o que as planilhas e o banco usam para dizer "não tem" ("ver NF" é o custo vazio do catálogo)
VAZIOS = {"", "-", "?", "n/a", "na", "nan", "none", "null", "nao tem", "não tem", "ver nf"}
# cliente_ref de venda sem cliente identificado
SEM_CLIENTE = {"nao informado", "não informado", "consumidor"}


def vazio(valor) -> bool:
    if valor is None:
        return True
    if isinstance(valor, float) and valor != valor:  # NaN do pandas
        return True
    return isinstance(valor, str) and valor.strip().lower() in VAZIOS


# texto de uma linha: tira espaço das pontas e junta espaços repetidos (" Maria  Souza " -> "Maria Souza")
def texto(valor) -> str | None:
    if vazio(valor):
        return None
    return re.sub(r"\s+", " ", str(valor)).strip()


# texto livre (mensagem de atendimento): só tira espaço das pontas, mantém as quebras de linha
def texto_longo(valor) -> str | None:
    if vazio(valor):
        return None
    return str(valor).strip()


# o SQLite da Vulto mistura UTF-8 com latin-1 ("S\xe3o Paulo"); tenta UTF-8 e cai para latin-1
def decodificar(valor):
    if not isinstance(valor, bytes):
        return valor
    try:
        return valor.decode("utf-8")
    except UnicodeDecodeError:
        return valor.decode("latin-1")


# ---------- nomes ----------

PARTICULAS = {"da", "de", "do", "das", "dos", "e"}


# "ALINE BARROS" / "marina dias" -> "Aline Barros" / "Marina Dias"; partículas ficam minúsculas
def nome_proprio(valor) -> str | None:
    t = texto(valor)
    if t is None:
        return None
    palavras = t.lower().split(" ")
    return " ".join(p if i > 0 and p in PARTICULAS else p.capitalize() for i, p in enumerate(palavras))


# chave só para comparar nomes (sem acento e minúscula); não é o nome que vai para o banco
def chave_busca(valor) -> str | None:
    t = texto(valor)
    if t is None:
        return None
    sem_acento = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode("ascii")
    return sem_acento.lower()


# ---------- documentos e contatos ----------

# CPF com só os 11 dígitos. Aceita máscara e espaços. Os zeros à esquerda só voltam quando a célula é
# número (o Excel come os zeros: 463337549 -> 00463337549); texto com menos de 11 dígitos vira pendência,
# porque no banco esses valores costumam ser celular sem DDD. Dígito verificador errado também é pendência.
def cpf(valor) -> str | None:
    if vazio(valor):
        return None
    if isinstance(valor, float):
        if not valor.is_integer():
            raise ValorInvalido("CPF com casas decimais")
        valor = int(valor)
    digitos = normalizar_cpf(str(valor))
    if isinstance(valor, int) and 9 <= len(digitos) <= 10:
        digitos = digitos.zfill(11)
    if len(digitos) != 11:
        raise ValorInvalido("CPF sem 11 dígitos (pode ser telefone)")
    if not cpf_valido(digitos):
        raise ValorInvalido("CPF com dígito verificador errado")
    return digitos


# CNPJ com só os 14 dígitos (pedido corporativo aparece no cliente_ref)
def cnpj(valor) -> str | None:
    if vazio(valor):
        return None
    digitos = re.sub(r"\D", "", str(valor))
    if len(digitos) != 14 or digitos == digitos[0] * 14:
        raise ValorInvalido("CNPJ sem 14 dígitos")
    for tamanho_base in (12, 13):
        pesos = list(range(tamanho_base - 7, 1, -1)) + list(range(9, 1, -1))
        resto = sum(int(d) * p for d, p in zip(digitos, pesos)) % 11
        if digitos[tamanho_base] != str(0 if resto < 2 else 11 - resto):
            raise ValorInvalido("CNPJ com dígito verificador errado")
    return digitos


EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def email(valor) -> str | None:
    if vazio(valor):
        return None
    e = str(valor).strip().lower()
    if not EMAIL.match(e):
        raise ValorInvalido("e-mail fora do formato")
    return e


# telefone no formato +55DDDNUMERO. Telefone sem DDD vira pendência: não dá para saber a cidade
def telefone(valor) -> str | None:
    if vazio(valor):
        return None
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    digitos = re.sub(r"\D", "", str(valor))
    if len(digitos) in (12, 13) and digitos.startswith("55"):
        digitos = digitos[2:]
    if len(digitos) not in (10, 11):
        raise ValorInvalido("telefone sem DDD ou com dígitos a mais")
    return "+55" + digitos


MASCARA_CPF = re.compile(r"\d{3}\.\d{3}\.\d{3}-\d{2}|\d{3} \d{3} \d{3} \d{2}")
MASCARA_CNPJ = re.compile(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}")
MASCARA_TELEFONE = re.compile(r".*[()+].*|\d{4,5}-\d{4}|\d{2} \d{8,9}")  # "11 954070139": DDD separado


# cliente_ref da Vulto guarda CPF, e-mail, telefone ou @ no mesmo campo ("depende de quem cadastrou").
# Devolve (tipo, valor padronizado). Só classifica quando o formato não deixa dúvida: @, e-mail ou
# máscara de CPF, CNPJ ou telefone. Número só com dígitos sai como tipo "numero", sem decidir se é CPF
# ou celular (os dois têm 11 dígitos e ~1% dos celulares passa no dígito verificador do CPF): a regra
# fica para a etapa 2, cruzando com o cadastro (ver README). No atendimento não existe CPF, então
# aceita_cpf=False manda os números direto para telefone.
def contato(valor, aceita_cpf: bool = True) -> tuple[str | None, str | None]:
    t = texto(valor)
    if t is None or t.lower() in SEM_CLIENTE:
        return (None, None)
    if t.startswith("@"):
        return ("instagram", t.lower())
    if "@" in t:
        return ("email", email(t))
    if aceita_cpf and MASCARA_CPF.fullmatch(t):
        return ("cpf", cpf(t))
    if aceita_cpf and MASCARA_CNPJ.fullmatch(t):
        return ("cnpj", cnpj(t))
    if MASCARA_TELEFONE.fullmatch(t):
        return ("telefone", telefone(t))
    if not t.isdigit():
        raise ValorInvalido("contato em formato desconhecido")
    if aceita_cpf:
        return ("numero", t)
    return ("telefone", telefone(t))


# ---------- números ----------

# valor em reais como Decimal com 2 casas. "1.299,90" e "R$ 129,90" (padrão BR) e "589.70" (padrão do
# banco) dão o mesmo resultado. NÃO corrige o total do app gravado em centavos: isso é decisão à parte.
def decimal_br(valor) -> Decimal | None:
    if vazio(valor):
        return None
    if isinstance(valor, bool):
        raise ValorInvalido("valor booleano")
    if isinstance(valor, (int, float)):  # célula numérica da planilha
        return Decimal(str(valor)).quantize(CENTAVO)
    s = str(valor).replace("R$", "").replace(" ", "").strip()
    if "," in s:  # vírgula é decimal, ponto é milhar
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", s):  # "1.500": mil e quinhentos ou um e cinquenta?
        raise ValorInvalido("valor ambíguo (ponto pode ser milhar ou decimal)")
    try:
        return Decimal(s).quantize(CENTAVO)
    except InvalidOperation:
        raise ValorInvalido("valor não numérico")


# quantidade inteira (peças, pontos já numéricos). "1.200" ou "9,9k" não passam: são outro formato
def inteiro(valor) -> int | None:
    if vazio(valor):
        return None
    try:
        n = Decimal(str(valor).strip())
    except InvalidOperation:
        raise ValorInvalido("quantidade não numérica")
    if n != n.to_integral_value():
        raise ValorInvalido("quantidade com casas decimais")
    return int(n)


# Contagem física: além do número, aparece "2 (1 no estoque + 1 vitrine)". O total é o número antes
# do parêntese; só é aceito se as parcelas de dentro somam esse total
def quantidade_contada(valor) -> int | None:
    if isinstance(valor, str):
        m = re.fullmatch(r"\s*(\d+)\s*\((.*)\)\s*", valor)
        if m:
            total = int(m.group(1))
            if sum(int(p) for p in re.findall(r"\d+", m.group(2))) != total:
                raise ValorInvalido("parcelas da contagem não somam o total")
            return total
    return inteiro(valor)


# Pontos do resgate (K7b): ponto inteiro, então "2.000" e "1.500" têm ponto de milhar (2.000 pts, não 2).
# Só o formato com grupos de 3 dígitos depois do ponto; o resto segue a regra do inteiro
def pontos_milhar(valor) -> int | None:
    if isinstance(valor, str) and re.fullmatch(r"\s*\d{1,3}(\.\d{3})+\s*", valor):
        return int(valor.strip().replace(".", ""))
    return inteiro(valor)


# ---------- datas ----------

# Data e hora sempre em UTC. Com "Z" ou fuso explícito (-03:00) o fuso é o informado; sem fuso é
# horário de Brasília (é o datetime.now() do PDV das lojas). "DD/MM/AAAA HH:MM" (Savassi) é dia primeiro.
def data_hora(valor) -> datetime | None:
    if vazio(valor):
        return None
    if isinstance(valor, datetime):
        dt = valor
    else:
        s = str(valor).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            dt = _primeiro_formato(s, ["%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S"])
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BRASILIA)
    return dt.astimezone(timezone.utc)


EPOCA_EXCEL = date(1899, 12, 30)


# Só a data. Aceita datetime da planilha, número de série do Excel (44351 = 02/06/2021) e texto em
# DD/MM/AAAA, AAAA-MM-DD ou DD/MM/AA. Barra é sempre dia primeiro (padrão BR em todos os arquivos).
def data(valor) -> date | None:
    if vazio(valor):
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    if isinstance(valor, str) and valor.strip().isdigit():
        valor = int(valor.strip())
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        if not 20000 <= valor <= 60000:  # de 1954 a 2064; fora disso não é data do Excel
            raise ValorInvalido("número fora da faixa de datas do Excel")
        return EPOCA_EXCEL + timedelta(days=int(valor))
    s = str(valor).strip()
    return _primeiro_formato(s, ["%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y", "%Y-%m-%d %H:%M:%S"]).date()


MESES = {"jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
         "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12}


# Aba Resgates: além das datas comuns, tem "set/23" (só mês e ano). Devolve (data, só_mês); quando
# é só o mês, a data fica no dia 1 e só_mês=True avisa que o dia não é conhecido.
def data_ou_mes(valor) -> tuple[date | None, bool]:
    if isinstance(valor, str):
        m = re.fullmatch(r"([a-z]{3})/(\d{2}|\d{4})", valor.strip().lower())
        if m and m.group(1) in MESES:
            ano = int(m.group(2))
            return (date(ano + 2000 if ano < 100 else ano, MESES[m.group(1)], 1), True)
    return (data(valor), False)


def _primeiro_formato(s: str, formatos: list[str]) -> datetime:
    for formato in formatos:
        try:
            return datetime.strptime(s, formato)
        except ValueError:
            pass
    raise ValorInvalido("data em formato desconhecido")


# ---------- catálogo ----------

# SKU sem espaço nas pontas e em maiúsculas. Não converte para o padrão VLT-CAT-NNN-COR-TAM:
# o código curto e o digitado pelo fiscal precisam ser conferidos contra o catálogo (regra 8 da Marina)
def sku(valor) -> str | None:
    t = texto(valor)
    return t.upper() if t else None


# EAN-13 como texto (o Excel guarda como número). O genérico 789 + zeros é "sem EAN"
def ean13(valor) -> str | None:
    if vazio(valor):
        return None
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    digitos = re.sub(r"\D", "", str(valor))
    if len(digitos) != 13:
        raise ValorInvalido("EAN sem 13 dígitos")
    if digitos[3:12] == "0" * 9:
        return None
    return digitos


# tamanho como texto em maiúsculas: 36.0 -> "36", "m" -> "M"
def tamanho(valor) -> str | None:
    if vazio(valor):
        return None
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    return str(valor).strip().upper()


# ---------- atendimento ----------

# "troca;tamanho", "TROCA, TAMANHO" -> ["troca", "tamanho"] (sem repetir, na ordem em que aparecem)
def tags(valor) -> list[str]:
    if vazio(valor):
        return []
    vistas = []
    for tag in re.split(r"[;,]", str(valor)):
        tag = tag.strip().lower()
        if tag and tag not in vistas:
            vistas.append(tag)
    return vistas


FORMATOS_PEDIDO = [("site", re.compile(r"S\d+")), ("app", re.compile(r"APP-\d+")), ("numero", re.compile(r"\d+"))]


# Classifica o número de pedido citado no atendimento (S100234 = site, APP-7001324 = app, número solto
# = id interno ou digitado pelo cliente). O prefixo decide o tipo; se a quantidade de dígitos não for a
# usual (APP-4904), o código fica como veio e o vínculo com o pedido é que vai falhar. Tira o "#" e a palavra "pedido" que o atendente escreveu antes
# ("#1094", "pedido 1146"). Só classifica: ligar ao pedido certo é a próxima etapa.
def pedido(valor) -> tuple[str | None, str | None]:
    t = texto(valor)
    if t is None:
        return (None, None)
    codigo = re.sub(r"^(PEDIDO|PED\.?|N[º°O]\.?)\s*", "", t.upper()).lstrip("#").replace(" ", "")
    for tipo, padrao in FORMATOS_PEDIDO:
        if padrao.fullmatch(codigo):
            return (tipo, codigo)
    raise ValorInvalido("número de pedido em formato desconhecido")
