# Calcula os três números da Entrega 3 a partir do staging (etapa 1), aplicando as decisões do
# registro (registro_decisoes.md). Cada regra cita a decisão que a define (V1, K3, E5...).
# Grava só agregados (por mês, loja, canal, produto), sem CPF nem e-mail, em --destino.
# Uso, na raiz do repositório com o venv ativo:
#   python -m migracao_vulto.numeros --origem staging_vulto --destino numeros_vulto
import argparse
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

FUSO_BRASILIA = ZoneInfo("America/Sao_Paulo")
INICIO, FIM = "2025-01", "2026-09"  # período do faturamento pedido pela Entrega 3

LOJAS_FISICAS = {"1": "Vulto Pinheiros", "2": "Vulto Shopping Eldorado", "3": "Vulto Savassi", "4": "Vulto Batel"}
CD = "CD Cajamar"  # site e app vendem do CD (V12)
LOJA_BATEL = "4"

# pedido do app em centavos: a mediana da razão preço do item / preço do catálogo passa disso (V1).
# Em reais a razão fica perto de 1 e em centavos perto de 100; as 27 com razão 50 são 50% de desconto
ESCALA_CENTAVOS = 5


# ---------- SKU e catálogo ----------

# Chave comum do SKU nos formatos longo, curto e com espaço (VLT-CAM-004-BRA-M = CAM004BM =
# "vlt cam 004 branco m"): categoria + número + letra da cor + tamanho. Serve só para achar o preço
# do catálogo; a conversão oficial é a J2.
def chave_sku(sku) -> str | None:
    s = str(sku).upper().strip()
    longo = re.fullmatch(r"VLT[- ]([A-Z]{3})[- ](\d{3})[- ]([A-Z]+)[- ]?(.*)", s)
    if longo:
        return f"{longo[1]}{longo[2]}{longo[3][0]}{longo[4].replace(' ', '').replace('-', '')}"
    curto = re.fullmatch(r"([A-Z]{3})(\d{3})([A-Z])(.+)", s)
    if curto:
        return f"{curto[1]}{curto[2]}{curto[3]}{curto[4]}"
    return None


# Preço de referência por chave e, quando a variante não está no catálogo, pelo modelo (6 primeiros)
def precos_referencia(catalogo: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    c = catalogo.assign(chave=catalogo["sku"].map(chave_sku), preco=pd.to_numeric(catalogo["preco"]))
    c = c.dropna(subset=["chave"])
    return c.groupby("chave")["preco"].max(), c.groupby(c["chave"].str[:6])["preco"].max()


# ---------- vendas: classificação ----------

# Situação do pedido a partir das grafias do status (V13). Vazio só existe na loja física em
# jan–fev/2025 e conta como pago (V2)
def situacao(status) -> str:
    s = "" if pd.isna(status) else str(status).strip().lower()
    if s in ("", "pago", "aprovado"):
        return "pago"
    if s.startswith("cancel"):
        return "cancelado"
    if s in ("aguardando", "estornado", "chargeback"):
        return s
    raise ValueError(f"status desconhecido: {status!r}")


# Venda de teste em produção (V8): obs "teste", e-mails internos, CPF de teste e os códigos 99xxx
def eh_teste(obs, cliente_ref, cliente_valor, pedido) -> bool:
    obs = "" if pd.isna(obs) else str(obs).strip().lower()
    ref = "" if pd.isna(cliente_ref) else str(cliente_ref).lower()
    pedido = "" if pd.isna(pedido) else str(pedido)
    return (obs in ("teste", "teste qa") or "teste@" in ref or "qa.app@" in ref
            or cliente_valor == "12345678909" or pedido.startswith(("APP-990", "S990")))


# Tipo do lançamento (V6): venda negativa é devolução; obs começando com "troca" é troca
def tipo_lancamento(total: float, obs) -> str:
    if total < 0:
        return "devolucao"
    if not pd.isna(obs) and str(obs).strip().lower().startswith("troca"):
        return "troca"
    return "venda"


# Canal de venda (P3): o código do pedido separa site e app, não o loja_id (222 do app na loja 5)
def canal_venda(loja_id, pedido) -> str:
    pedido = "" if pd.isna(pedido) else str(pedido)
    if pedido.startswith("APP-"):
        return "app"
    if str(loja_id) in LOJAS_FISICAS:
        return "loja"
    return "site"


# Mês da venda no horário de Brasília (V16); o relatório da Vulto agrupa em UTC
def mes_brasilia(criado_em: str) -> str:
    return datetime.fromisoformat(criado_em).astimezone(FUSO_BRASILIA).strftime("%Y-%m")


def mes_utc(criado_em: str) -> str:
    return str(criado_em)[:7]


# Escala de cada pedido do app (V1): divide por 100 quando a mediana da razão item / catálogo passa
# de ESCALA_CENTAVOS. Devolve {venda_id: True se está em centavos}
def pedidos_em_centavos(vendas: pd.DataFrame, itens: pd.DataFrame, catalogo: pd.DataFrame) -> dict:
    por_chave, por_modelo = precos_referencia(catalogo)
    app = vendas.loc[vendas["pedido_externo"].fillna("").str.startswith("APP-"), "id"]
    it = itens[itens["venda_id"].isin(app)].copy()
    chave = it["sku"].map(chave_sku)
    referencia = chave.map(por_chave).fillna(chave.str[:6].map(por_modelo))
    razao = (pd.to_numeric(it["preco_unit"]).abs() / referencia).groupby(it["venda_id"]).median()
    sem_referencia = set(app) - set(razao.dropna().index)
    if sem_referencia:
        raise ValueError(f"pedidos do app sem preço de catálogo para decidir a escala: {sorted(sem_referencia)}")
    return (razao > ESCALA_CENTAVOS).to_dict()


# ---------- faturamento ----------

# Uma linha por venda do banco com o valor no relatório da Vulto, o valor corrigido (V1), o valor no
# nosso faturamento, o frete à parte (V7) e o motivo quando a venda não conta
def lancamentos_banco(vendas: pd.DataFrame, itens: pd.DataFrame, catalogo: pd.DataFrame) -> pd.DataFrame:
    v = vendas.copy()
    for coluna in ("total", "desconto", "frete"):
        v[coluna] = pd.to_numeric(v[coluna]).fillna(0.0)
    v["teste"] = [eh_teste(*r) for r in zip(v["obs"], v["cliente_ref"], v["cliente_valor"], v["pedido_externo"])]
    # as vendas de teste (SKU TESTE-001) não têm preço de catálogo e não entram em nada
    centavos = pedidos_em_centavos(v[~v["teste"]], itens, catalogo)
    v["em_centavos"] = v["id"].map(lambda i: centavos.get(i, False))
    escala = v["em_centavos"].map({True: 0.01, False: 1.0})
    v["corrigido"] = (v["total"] * escala).round(2)

    # frete: no site está na coluna; no app vem embutido no total (total − itens + desconto, V7)
    valor_itens = (pd.to_numeric(itens["qtd"]) * pd.to_numeric(itens["preco_unit"])).groupby(itens["venda_id"]).sum()
    embutido = (v["total"] - v["id"].map(valor_itens).fillna(0) + v["desconto"]) * escala
    eh_app = v["pedido_externo"].fillna("").str.startswith("APP-")
    v["frete_venda"] = v["frete"].where(~eh_app, embutido.clip(lower=0)).round(2)

    v["situacao"] = v["status"].map(situacao)
    v["tipo"] = [tipo_lancamento(t, o) for t, o in zip(v["total"], v["obs"])]
    v["canal"] = [canal_venda(l, p) for l, p in zip(v["loja_id"], v["pedido_externo"])]
    v["unidade"] = v["loja_id"].map(LOJAS_FISICAS).fillna(CD)
    v["mes"] = v["criado_em"].map(mes_brasilia)
    v["mes_utc"] = v["criado_em"].map(mes_utc)
    v["no_relatorio"] = v["criado_em"].str[:10].between(f"{INICIO}-01", f"{FIM}-30")

    # cópia do pedido gravada duas vezes (V5): fica a última linha de cada código
    ordem = v.sort_values("criado_em")
    copia = ordem["pedido_externo"].notna() & ordem.duplicated("pedido_externo", keep="last")
    v["copia"] = copia.reindex(v.index)

    # relançamento da Batel (V9): toda venda da gerente entre 23/03 e 02/04/2026; vale a planilha
    gerente = v["vendedor"].fillna("").str.strip().str.lower().isin(["gerente", "gerente batel"])
    dia = v["criado_em"].map(lambda d: datetime.fromisoformat(d).astimezone(FUSO_BRASILIA).strftime("%Y-%m-%d"))
    v["relancamento_batel"] = (v["loja_id"] == LOJA_BATEL) & gerente & dia.between("2026-03-23", "2026-04-02")

    v["motivo"] = [motivo_fora(r) for r in v.itertuples()]
    conta = v["motivo"].isna()
    v["faturamento"] = (v["corrigido"] - v["frete_venda"]).where(conta, 0.0).round(2)
    v["frete_fat"] = v["frete_venda"].where(conta & (v["tipo"] == "venda"), 0.0)
    return v


# Por que a venda não entra no faturamento (None = entra). A ordem segue a do registro: teste e cópia
# não são venda; cancelado e aguardando não foram pagos; estorno e chargeback devolveram o dinheiro
# (o estorno "devolucao parcial" é venda negativa e segue a V6); troca não gera receita
def motivo_fora(venda) -> str | None:
    if venda.teste:
        return "V8 teste"
    if venda.copia:
        return "V5 cópia do pedido"
    if venda.relancamento_batel:
        return "V9 relançamento da Batel"
    if venda.situacao == "cancelado":
        return "V19 cancelada"
    if venda.situacao == "aguardando":
        return "V3 aguardando sem pagamento"
    if venda.situacao in ("estornado", "chargeback") and venda.tipo != "devolucao":
        return "V4 estorno e chargeback"
    if venda.tipo == "troca":
        return "V6 troca"
    return None


# Data da planilha manual da Batel (V9): o Excel trocou dia e mês das datas que leu como data
# ("2026-02-03 00:00:00" é 02/03) e as sem ano ("03/03") são de 2026
def data_batel(valor) -> str:
    s = str(valor).strip()
    trocada = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2}) 00:00:00", s)
    if trocada:
        return f"{trocada[1]}-{trocada[3]}-{trocada[2]}"
    dia_mes = re.fullmatch(r"(\d{2})/(\d{2})(?:/(\d{4}))?", s)
    if dia_mes:
        return f"{dia_mes[3] or '2026'}-{dia_mes[2]}-{dia_mes[1]}"
    raise ValueError(f"data da Batel em formato desconhecido: {valor!r}")


# Vendas da planilha da Batel (02–22/03/2026). Sem os subtotais; a cancelada e as estornadas na
# maquininha não somam, como nos subtotais da própria loja
def lancamentos_batel(batel: pd.DataFrame) -> pd.DataFrame:
    b = batel[batel["QTD"].notna()].copy()
    b["data"] = b["DATA"].map(data_batel)
    fora_do_periodo = b[~b["data"].between("2026-03-02", "2026-03-22")]
    if len(fora_do_periodo):
        raise ValueError(f"venda da planilha da Batel fora de 02–22/03: {fora_do_periodo['linha_origem'].tolist()}")
    obs = b["OBS"].fillna("").str.lower()
    b["motivo"] = None
    b.loc[obs.str.contains("cancel"), "motivo"] = "V19 cancelada"
    b.loc[obs.str.contains("estorn"), "motivo"] = "V4 estorno e chargeback"
    valor = pd.to_numeric(b["VALOR"])
    return pd.DataFrame({
        "linha_origem": b["linha_origem"], "mes": "2026-03", "mes_utc": "2026-03", "unidade": LOJAS_FISICAS[LOJA_BATEL],
        "canal": "loja", "tipo": "venda", "total": 0.0, "corrigido": valor, "no_relatorio": False,
        "motivo": b["motivo"], "faturamento": valor.where(b["motivo"].isna(), 0.0), "frete_fat": 0.0,
    })


# Faturamento por mês, unidade e canal, com o frete numa coluna à parte (V7)
def faturamento_mensal(lancamentos: pd.DataFrame) -> pd.DataFrame:
    no_periodo = lancamentos[lancamentos["mes"].between(INICIO, FIM)]
    return (no_periodo.groupby(["mes", "unidade", "canal"])[["faturamento", "frete_fat"]].sum().round(2)
            .rename(columns={"frete_fat": "frete"}).reset_index())


# Relatório da Vulto como o sistema calcula (/relatorio/faturamento em main.py): soma o total de toda
# venda do período, de qualquer status, agrupada pelo dia em UTC e pelo loja_id
def relatorio_vulto(lancamentos: pd.DataFrame) -> pd.Series:
    r = lancamentos[lancamentos["no_relatorio"]]
    return r.groupby("mes_utc")["total"].sum().round(2)


# Ponte do relatório da Vulto ao nosso faturamento, por decisão. Cada venda contribui com
# (total − corrigido) na V1, (corrigido − frete − faturamento) no seu motivo e o frete na V7;
# a planilha da Batel entra como acréscimo; o fuso (V16) é o que muda de mês
def ponte(lancamentos: pd.DataFrame) -> pd.DataFrame:
    no_periodo = lancamentos["mes"].between(INICIO, FIM)
    base = lancamentos[lancamentos["no_relatorio"] | no_periodo]
    passos = [("Relatório do sistema da Vulto", base.loc[base["no_relatorio"], "total"].sum())]
    banco = base[base["no_relatorio"]]
    passos.append(("V1 pedidos do app em centavos", -(banco["total"] - banco["corrigido"]).sum()))
    fora = banco[banco["motivo"].notna()]
    for motivo, grupo in fora.groupby("motivo"):
        if motivo.startswith("V9"):
            continue
        passos.append((motivo, -grupo["corrigido"].sum()))
    contam = banco[banco["motivo"].isna()]
    passos.append(("V7 frete à parte", -(contam["corrigido"] - contam["faturamento"]).sum()))
    planilha = base[~base["linha_origem"].astype(str).str.startswith("vendas#")]
    relancadas = banco[banco["motivo"] == "V9 relançamento da Batel"]
    passos.append(("V9 Batel: planilha no lugar dos relançamentos",
                   planilha["faturamento"].sum() - relancadas["corrigido"].sum()))
    so_nosso = base[~base["no_relatorio"] & no_periodo & base["linha_origem"].astype(str).str.startswith("vendas#")]
    passos.append(("V16 fuso: vendas de 30/09 à noite (01/10 em UTC)", so_nosso["faturamento"].sum()))
    tabela = pd.DataFrame(passos, columns=["passo", "valor"])
    nosso = base.loc[no_periodo.reindex(base.index), "faturamento"].sum()
    if abs(tabela["valor"].sum() - nosso) > 0.05:
        raise AssertionError(f"a ponte não fecha: {tabela['valor'].sum():.2f} × {nosso:.2f}")
    tabela.loc[len(tabela)] = ["Nosso faturamento", nosso]
    tabela["valor"] = tabela["valor"].round(2)
    return tabela


# Comparação mês a mês: relatório da Vulto (mês em UTC) × nosso (mês em Brasília)
def comparacao_mensal(lancamentos: pd.DataFrame) -> pd.DataFrame:
    nosso = faturamento_mensal(lancamentos).groupby("mes")["faturamento"].sum()
    tabela = pd.DataFrame({"relatorio_vulto": relatorio_vulto(lancamentos), "nosso": nosso}).fillna(0.0)
    tabela["diferenca"] = (tabela["nosso"] - tabela["relatorio_vulto"]).round(2)
    return tabela.rename_axis("mes").reset_index()


def calcular_faturamento(origem: Path, destino: Path):
    vendas = pd.read_csv(origem / "banco_vendas.csv", dtype=str)
    itens = pd.read_csv(origem / "banco_itens_venda.csv", dtype=str)
    catalogo = pd.read_csv(origem / "catalogo_produtos.csv", dtype=str)
    batel = pd.read_csv(origem / "batel_vendas_manuais.csv", dtype=str)
    lancamentos = pd.concat([lancamentos_banco(vendas, itens, catalogo), lancamentos_batel(batel)], ignore_index=True)

    mensal = faturamento_mensal(lancamentos)
    gravar(mensal, destino, "faturamento_mensal")
    gravar(mensal.pivot_table(index="mes", columns="canal", values="faturamento", aggfunc="sum", margins=True,
                              margins_name="total").round(2).reset_index(), destino, "faturamento_por_canal")
    gravar(mensal.pivot_table(index="mes", columns="unidade", values="faturamento", aggfunc="sum", margins=True,
                              margins_name="total").round(2).reset_index(), destino, "faturamento_por_unidade")
    gravar(comparacao_mensal(lancamentos), destino, "faturamento_comparacao_mensal")
    tabela = ponte(lancamentos)
    gravar(tabela, destino, "faturamento_ponte")
    print(tabela.to_string(index=False))
    return lancamentos[lancamentos["linha_origem"].astype(str).str.startswith("vendas#")]


# ---------- passivo de pontos ----------

DATA_SALDO_INICIAL = "2025-01-01"  # a coluna Pontos da planilha foi congelada nesta data (K5a)
CORTE_VALOR_PONTO = "2025-03-01"   # ponto ganho antes vale R$ 0,10; a partir daqui, R$ 0,05 (K3)
VALOR_PONTO = {"antigo": 0.10, "novo": 0.05}
CPF_TESTE = "12345678909"          # CPF de teste: não migra, nem os pontos (K8, V8)
RESGATE_MINIMO = 100               # regra do Vulto+; abaixo disso, "2.000" foi lido como 2 (K7b)
DDDS = {11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 24, 27, 28, 31, 32, 33, 34, 35, 37, 38, 41, 42, 43, 44, 45, 46,
        47, 48, 49, 51, 53, 54, 55, 61, 62, 63, 64, 65, 66, 67, 68, 69, 71, 73, 74, 75, 77, 79, 81, 82, 83, 84, 85,
        86, 87, 88, 89, 91, 92, 93, 94, 95, 96, 97, 98, 99}
LOJA_DO_RESGATE = {"Pinheiros": "1", "Eldorado": "2", "Savassi": "3", "Batel": "4"}


def so_digitos(valor) -> str | None:
    return re.sub(r"\D", "", valor) if isinstance(valor, str) else None


# Pontos da planilha: "2.203", "1389 pts", "15.000" (ponto de milhar) e "2,7k" (K5b: k = ×1000)
def pontos_planilha(valor) -> float:
    if pd.isna(valor):
        return 0.0
    s = str(valor).lower().replace("pts", "").strip()
    if s.endswith("k"):
        return float(s[:-1].replace(",", ".")) * 1000
    return float(s.replace(".", ""))


# Época do ponto pela data em que foi ganho (K3)
def epoca(quando: str) -> str:
    return "antigo" if str(quando)[:10] < CORTE_VALOR_PONTO else "novo"


# CPFs válidos que diferem em só 1 dígito do digitado (K2); só serve quando há exatamente um
def candidatos_um_digito(digitado: str, conhecidos: set) -> list:
    return [c for c in conhecidos if len(c) == len(digitado) and sum(a != b for a, b in zip(c, digitado)) == 1]


class Identidades:
    """Acha o CPF do dono de um número digitado nos pontos (J1c, J1d, K2), a partir do cadastro."""

    def __init__(self, clientes: pd.DataFrame, pendencias: pd.DataFrame, conhecidos: set):
        self.conhecidos = conhecidos
        tel = clientes.dropna(subset=["cpf"]).assign(tel=clientes["Tel"].map(so_digitos).str[-11:])
        self.dono_telefone = tel.dropna(subset=["tel"]).groupby("tel")["cpf"].agg(set)
        self.dono_nove = tel.dropna(subset=["tel"]).groupby(tel["tel"].str[-9:])["cpf"].agg(set)
        # telefone do cadastro anotado sem DDD (K9a): o staging apagou, o valor está nas pendências
        sem_ddd = pendencias[(pendencias["coluna"] == "Tel") & pendencias["motivo"].str.contains("DDD")]
        sem_ddd = sem_ddd.assign(nove=sem_ddd["valor"].map(so_digitos).str[-9:],
                                 cpf=sem_ddd["linha"].map(clientes.set_index("linha_origem")["cpf"]))
        self.dono_sem_ddd = sem_ddd.dropna(subset=["cpf"]).groupby("nove")["cpf"].agg(set)

    @staticmethod
    def _unico(donos) -> str | None:
        return next(iter(donos)) if isinstance(donos, set) and len(donos) == 1 else None

    # Número de 11 dígitos com dígito verificador errado: preenchimento, telefone do cadastro (J1c),
    # celular (J1c: sem DDD do cadastro ou a 1 dígito de um CPF) ou CPF digitado errado (K2).
    # Devolve (cpf ou None, regra)
    def onze_digitos(self, digitado: str, com_mascara: bool) -> tuple[str | None, str]:
        if len(set(digitado)) == 1:
            return None, "K2 CPF de preenchimento"
        if digitado in self.dono_telefone.index:
            return self._unico(self.dono_telefone[digitado]), "J1c telefone do cadastro"
        candidatos = candidatos_um_digito(digitado, self.conhecidos)
        if int(digitado[:2]) in DDDS and digitado[2] == "9" and not com_mascara:
            dono = self._unico(self.dono_sem_ddd.get(digitado[-9:]))
            if dono:
                return dono, "J1c celular sem DDD no cadastro"
            return (candidatos[0] if len(candidatos) == 1 else None), "J1c celular a 1 dígito de um CPF"
        return (candidatos[0] if len(candidatos) == 1 else None), "K2 CPF digitado errado"

    # Celular digitado sem DDD (J1d): os 9 últimos dígitos de um telefone do cadastro, com ou sem DDD
    def nove_digitos(self, digitado: str) -> tuple[str | None, str]:
        nove = digitado[-9:]
        dono = self._unico(self.dono_nove.get(nove)) or self._unico(self.dono_sem_ddd.get(nove))
        return dono, "J1d celular sem DDD"


# CPF do dono de cada movimento de pontos e a regra que o achou. O staging deixa o cpf vazio quando
# não é CPF válido; o valor digitado está nas pendências
def dono_dos_pontos(pontos: pd.DataFrame, compras: pd.DataFrame, pendencias: pd.DataFrame,
                    ident: Identidades) -> pd.DataFrame:
    p = pontos.copy()
    digitado = pendencias[pendencias["fonte"] == "vulto.db"].set_index("linha")["valor"].map(so_digitos)
    p["digitado"] = p["linha_origem"].map(digitado)
    com_mascara = set(compras.loc[compras["cliente_ref"].str.contains(r"\d{3}\.\d{3}\.\d{3}-\d{2}", na=False), "id"])
    p["compra"] = p["compra_id"].str.removesuffix(".0")
    donos, regras = [], []
    for cpf, dig, compra in zip(p["cpf"], p["digitado"], p["compra"]):
        if isinstance(cpf, str):
            donos.append(None if cpf == CPF_TESTE else cpf)
            regras.append("V8 CPF de teste" if cpf == CPF_TESTE else "CPF")
        elif isinstance(dig, str) and len(dig) == 11:
            dono, regra = ident.onze_digitos(dig, compra in com_mascara)
            donos.append(dono)
            regras.append(regra)
        elif isinstance(dig, str):
            dono, regra = ident.nove_digitos(dig)
            donos.append(dono)
            regras.append(regra)
        else:
            donos.append(None)
            regras.append("sem identificador")
    p["dono"], p["regra_dono"] = donos, regras
    p["q"] = pd.to_numeric(p["qtd"])
    p["mov"] = p["q"].where(p["tipo"] != "D", -p["q"])  # D vem positivo e subtrai
    return p


# Saldo da planilha por CPF (K5a, K1: soma todos os cadastros), com o CPF do dígito errado corrigido
# (K2), mais os ATIVOS só da aba antiga (K5d). Devolve (saldo por CPF, pontos sem dono)
def saldo_inicial(clientes: pd.DataFrame, antigos: pd.DataFrame, pendencias: pd.DataFrame,
                  conhecidos: set) -> tuple[pd.Series, float]:
    c = clientes.assign(pts=clientes["Pontos"].map(pontos_planilha))
    errado = pendencias[(pendencias["coluna"] == "cpf") & pendencias["fonte"].str.startswith("clientes")]
    digitado = c["linha_origem"].map(errado.set_index("linha")["valor"].map(so_digitos))
    corrigido = digitado.map(lambda d: (lambda cs: cs[0] if len(cs) == 1 else None)(candidatos_um_digito(d, conhecidos))
                             if isinstance(d, str) else None)
    c["cpf"] = c["cpf"].fillna(corrigido)
    sem_dono = c.loc[c["cpf"].isna(), "pts"].sum()
    planilha = c.dropna(subset=["cpf"]).groupby("cpf")["pts"].sum()
    so_antiga = antigos[~antigos["CPF/CNPJ"].isin(clientes["cpf"]) & (antigos["STATUS"] == "ATIVO")]
    ativos = pd.to_numeric(so_antiga["PONTOS_2023"]).groupby(so_antiga["CPF/CNPJ"]).sum()
    return planilha.add(ativos, fill_value=0), sem_dono


# Créditos que não valem e ainda não foram estornados (K6), com quanto falta estornar de cada um.
# `vendas` já vem classificada por lancamentos_banco (situação, cópia, teste)
def creditos_a_estornar(p: pd.DataFrame, compras: pd.DataFrame, vendas: pd.DataFrame) -> pd.Series:
    comp = compras.assign(v=pd.to_numeric(compras["valor"])).set_index("id")
    cred = p[p["tipo"] == "C"].copy()
    cred["v"] = cred["compra"].map(comp["v"])
    cred["origem"] = cred["compra"].map(comp["origem"])

    v = vendas.dropna(subset=["compra_id"]).assign(cid=lambda d: d["compra_id"].str.removesuffix(".0"))
    nao_vale = v["teste"] | v["copia"] | v["situacao"].isin(["cancelado", "aguardando"]) | (
        v["situacao"].isin(["estornado", "chargeback"]) & (v["tipo"] != "devolucao"))
    invalidas = set(v.loc[nao_vale, "cid"])
    # loja física: a compra não aponta para a venda; liga a cancelada pelo cliente, valor, loja e dia
    fis = vendas[vendas["loja_id"].isin(list(LOJAS_FISICAS)) & (vendas["situacao"] == "cancelado")]
    chave_fis = set(zip(fis["cliente_ref"], fis["total"].round(2), fis["loja_id"], fis["criado_em"].str[:10]))
    for cid, linha in comp[comp["origem"] == "pdv"].iterrows():
        if (linha["cliente_ref"], round(linha["v"], 2), linha["loja_id"], str(linha["criado_em"])[:10]) in chave_fis:
            invalidas.add(cid)

    ja_estornado = p[p["tipo"] == "E"].groupby("compra")["q"].sum()  # E vem negativo
    # bug do app (jun/2026): crédito sobre o valor em centavos; vale 1% do crédito, como a Vulto corrigiu
    inflado = (cred["origem"] == "app") & (cred["v"] > 3000) & (cred["q"] / cred["v"] >= 0.9)
    corrigidos = set(p.loc[(p["tipo"] == "AJ") & p["obs"].fillna("").str.contains("bug"), "cpf"])
    sem_bug = cred["q"] - (cred["q"] / 100).round()
    ajuste_feito = sem_bug.where(inflado & cred["cpf"].isin(corrigidos), 0)
    falta = pd.Series(0.0, index=cred.index)
    invalido = cred["compra"].isin(invalidas)
    falta[invalido] = cred["q"] + cred["compra"].map(ja_estornado).fillna(0) - ajuste_feito
    falta[~invalido & inflado & ~cred["cpf"].isin(corrigidos)] = sem_bug
    return falta[falta > 0]


# Créditos do online que não ganharam o dobro por causa da grafia do canal (K4b): falta o mesmo número
# de pontos. Abaixo de 0,6 ponto por real, o valor da compra ainda está em centavos (V1)
def creditos_sem_dobro(p: pd.DataFrame, compras: pd.DataFrame, estornados: pd.Series) -> pd.Series:
    comp = compras.assign(v=pd.to_numeric(compras["valor"])).set_index("id")
    cred = p[(p["tipo"] == "C") & p["compra"].map(comp["origem"]).isin(["site", "app"])]
    por_real = cred["q"] / cred["compra"].map(comp["v"])
    com_dobro = por_real.where(por_real >= 0.6, por_real * 100).round(1) >= 1.9
    sem = cred[~com_dobro & ~cred.index.isin(estornados.index)]
    return sem["q"]


# Pontos usados no resgate da planilha: "1.500" ficou nas pendências e "2.000" foi lido como 2 (K7b)
def pontos_do_resgate(resgates: pd.DataFrame, pendencias: pd.DataFrame) -> pd.Series:
    pts = pd.to_numeric(resgates["pontos usados"])
    pend = pendencias[pendencias["coluna"] == "pontos usados"].set_index("linha")["valor"]
    da_pendencia = resgates["linha_origem"].map(pend).map(lambda s: float(s.replace(".", "")) if isinstance(s, str) else None)
    pts = pts.fillna(da_pendencia)
    return pts.where(pts >= RESGATE_MINIMO, pts * 1000)


# Resgates anotados só na planilha desde 2025 (K7a): com CPF e fora do banco (mesmo CPF e quantidade,
# até 3 dias); sem CPF, o cliente vem da venda com o mesmo desconto, loja e até 1 dia, confirmado pelo nome
def resgates_so_na_planilha(resgates: pd.DataFrame, pendencias: pd.DataFrame, p: pd.DataFrame,
                            vendas: pd.DataFrame, clientes: pd.DataFrame) -> pd.DataFrame:
    from migracao_vulto.normalizadores import chave_busca

    r = resgates.assign(pts=pontos_do_resgate(resgates, pendencias), desconto=pd.to_numeric(resgates["desconto R$"]),
                        dia=pd.to_datetime(resgates["data"], errors="coerce"))
    r = r[r["dia"] >= DATA_SALDO_INICIAL]
    banco = p[p["tipo"] == "D"].assign(dia=pd.to_datetime(p["criado_em"]).dt.tz_convert(FUSO_BRASILIA)
                                       .dt.tz_localize(None).dt.normalize())
    no_banco = [((banco["cpf"] == c) & (banco["q"] == q) & ((banco["dia"] - d).dt.days.abs() <= 3)).any()
                for c, q, d in zip(r["cliente_cpf"], r["pts"], r["dia"])]
    com_cpf = r[r["cliente_cpf"].notna() & ~pd.Series(no_banco, index=r.index)]

    v = vendas.assign(dia=pd.to_datetime(vendas["criado_em"]).dt.tz_convert(FUSO_BRASILIA).dt.tz_localize(None).dt.normalize(),
                      ref=vendas["cliente_ref"].map(so_digitos))
    nomes = clientes.dropna(subset=["cpf"]).groupby("cpf")["nome_busca"].agg(set)

    def confirma(nome, cpf):  # nome completo igual ao do cadastro; só o primeiro nome, igual ao primeiro
        return any(n == nome if " " in nome else n.split()[0] == nome for n in nomes.get(cpf, set()))

    sem = r[r["cliente_cpf"].isna()].copy()
    achados = []
    for nome, loja, desconto, dia in zip(sem["cliente_nome"].map(chave_busca), sem["loja"], sem["desconto"], sem["dia"]):
        candidatas = v[(v["loja_id"] == LOJA_DO_RESGATE.get(loja)) & ((v["dia"] - dia).dt.days.abs() <= 1)
                       & ((v["desconto"] - desconto).abs() < 0.01)]
        cpfs = {c for c in candidatas["ref"].dropna() if confirma(nome, c)}
        achados.append(next(iter(cpfs)) if len(cpfs) == 1 else None)
    sem["cliente_cpf"] = achados
    return pd.concat([com_cpf.assign(regra="K7a com CPF"), sem.assign(regra="K7a sem CPF, pela venda")])


# Valor em R$ do saldo de cada cliente pela K3, gastando primeiro os pontos mais antigos (FIFO).
# Estorno e ajuste ligados a um crédito tiram da época desse crédito; o resgate segue o FIFO
def valorar_fifo(movimentos: pd.DataFrame) -> pd.DataFrame:
    saldos = {}
    for cpf, d in movimentos.sort_values("quando", kind="stable").groupby("cpf"):
        pote = {"antigo": 0.0, "novo": 0.0}
        for mov, epoca_mov, fifo in zip(d["mov"], d["epoca"], d["fifo"]):
            if mov >= 0:
                pote[epoca_mov] += mov
                continue
            falta = -mov
            ordem = ["antigo", "novo"] if fifo else [epoca_mov] + [e for e in ("antigo", "novo") if e != epoca_mov]
            for qual in ordem:
                gasto = min(pote[qual], falta)
                pote[qual] -= gasto
                falta -= gasto
            pote["novo"] -= falta  # o que sobrar deixa o saldo negativo (K5f trata depois)
        saldos[cpf] = pote
    tabela = pd.DataFrame.from_dict(saldos, orient="index", columns=["antigo", "novo"])
    tabela["pontos"] = tabela["antigo"] + tabela["novo"]
    return tabela


def calcular_passivo(origem: Path, destino: Path, vendas_classificadas: pd.DataFrame):
    clientes = pd.read_csv(origem / "clientes_clientes.csv", dtype=str)
    antigos = pd.read_csv(origem / "clientes_antigos.csv", dtype=str)
    resgates = pd.read_csv(origem / "clientes_resgates.csv", dtype=str)
    pontos = pd.read_csv(origem / "banco_pontos_mov.csv", dtype=str)
    compras = pd.read_csv(origem / "banco_compras.csv", dtype=str)
    pendencias = pd.read_csv(origem / "pendencias.csv", dtype=str)
    vendas = vendas_classificadas

    from src.utils.cpf import cpf_valido
    conhecidos = set()
    for serie in [clientes["cpf"], pontos["cpf"], resgates["cliente_cpf"], antigos["CPF/CNPJ"],
                  compras["cliente_ref"], vendas["cliente_ref"]]:
        conhecidos |= {c for c in serie.map(so_digitos).dropna() if cpf_valido(c)}
    conhecidos.discard(CPF_TESTE)

    ident = Identidades(clientes, pendencias, conhecidos)
    p = dono_dos_pontos(pontos, compras, pendencias, ident)
    inicial, sem_dono_planilha = saldo_inicial(clientes, antigos, pendencias, conhecidos)
    estornar = creditos_a_estornar(p, compras, vendas)
    dobro = creditos_sem_dobro(p, compras, estornar)
    so_planilha = resgates_so_na_planilha(resgates, pendencias, p, vendas, clientes)

    # todos os movimentos do passivo, cada um com a decisão que o gerou
    p["quando"] = p["criado_em"].map(lambda d: datetime.fromisoformat(d).astimezone(FUSO_BRASILIA).strftime("%Y-%m-%d %H:%M:%S"))
    credito_quando = p["quando"]
    partes = [
        pd.DataFrame({"cpf": inicial.index, "mov": inicial.values, "quando": DATA_SALDO_INICIAL,
                      "decisao": "K5a/K5d saldo da planilha", "fifo": False}),
        pd.DataFrame({"cpf": p["dono"], "mov": p["mov"], "quando": p["quando"],
                      "decisao": "Movimentos do banco (" + p["tipo"] + ")", "fifo": p["tipo"] == "D",
                      "regra_dono": p["regra_dono"]}),
        pd.DataFrame({"cpf": p.loc[dobro.index, "dono"], "mov": dobro.values, "quando": credito_quando[dobro.index],
                      "decisao": "K4b dobro do online", "fifo": False}),
        pd.DataFrame({"cpf": p.loc[estornar.index, "dono"], "mov": -estornar.values, "quando": credito_quando[estornar.index],
                      "decisao": "K6 pontos de venda que não vale", "fifo": False}),
        pd.DataFrame({"cpf": so_planilha["cliente_cpf"], "mov": -so_planilha["pts"].values,
                      "quando": so_planilha["dia"].dt.strftime("%Y-%m-%d"), "decisao": "K7a resgate só na planilha",
                      "fifo": True}),
    ]
    movimentos = pd.concat(partes, ignore_index=True)
    movimentos["epoca"] = movimentos["quando"].map(epoca)
    movimentos.loc[movimentos["decisao"].str.startswith("K5"), "epoca"] = "antigo"
    sem_dono = movimentos[movimentos["cpf"].isna()]
    movimentos = movimentos.dropna(subset=["cpf"])

    saldos = valorar_fifo(movimentos)
    negativos = saldos[saldos["pontos"] < 0]
    saldos.loc[negativos.index, ["antigo", "novo", "pontos"]] = 0.0  # K5f: zera o saldo negativo herdado
    saldos["reais"] = saldos["antigo"] * VALOR_PONTO["antigo"] + saldos["novo"] * VALOR_PONTO["novo"]

    resumo = movimentos.groupby("decisao")["mov"].agg(["count", "sum"]).rename(columns={"count": "lancamentos", "sum": "pontos"})
    resumo.loc["K5f ajuste dos saldos negativos"] = [len(negativos), -negativos["pontos"].sum()]
    resumo.loc["Passivo em 01/10/2026"] = [len(saldos[saldos["pontos"] > 0]), saldos["pontos"].sum()]
    gravar(resumo.round(0).reset_index(), destino, "passivo_por_decisao")

    total_pts, total_reais = saldos["pontos"].sum(), saldos["reais"].sum()
    # margem de erro: ponto que pode ser dívida sem dono (K2), resgate dado sem saber de quem (K7a) e o
    # arredondamento do "2,7k" (K5b: até ±50 pts por saldo abreviado). O CPF de teste fica fora por decisão
    sem_dono_k2 = sem_dono.loc[sem_dono["regra_dono"].fillna("").str.startswith("K2"), "mov"].sum() + sem_dono_planilha
    sem_cliente_k7a = sem_dono.loc[sem_dono["decisao"].str.startswith("K7a"), "mov"].sum()
    abreviados = clientes["Pontos"].fillna("").str.lower().str.endswith("k").sum() * 50
    valor = pd.DataFrame([
        ("pontos ganhos até fev/2025 (R$ 0,10)", saldos["antigo"].sum(), saldos["antigo"].sum() * 0.10),
        ("pontos ganhos desde mar/2025 (R$ 0,05)", saldos["novo"].sum(), saldos["novo"].sum() * 0.05),
        ("Passivo em 01/10/2026 (K3, FIFO)", total_pts, total_reais),
        ("Sensibilidade: tudo a R$ 0,10 (valor do sistema)", total_pts, total_pts * 0.10),
        ("Sensibilidade: tudo a R$ 0,05 (regra atual)", total_pts, total_pts * 0.05),
        ("Margem: pontos sem dono, podem ser dívida (K2)", sem_dono_k2, sem_dono_k2 * 0.10),
        ("Margem: resgates sem cliente, podem reduzir a dívida (K7a)", sem_cliente_k7a, sem_cliente_k7a * 0.05),
        ("Margem: saldos abreviados, para mais ou para menos (K5b)", abreviados, abreviados * 0.10),
    ], columns=["item", "pontos", "reais"]).round(2)
    gravar(valor, destino, "passivo_valor")
    gravar(sem_dono.groupby(sem_dono["regra_dono"].fillna(sem_dono["decisao"]))["mov"].agg(["count", "sum"])
           .reset_index(), destino, "passivo_sem_dono")
    print(resumo.round(0).to_string())
    print(valor.to_string(index=False))
    return saldos


# ---------- estoque ----------

DATA_BASE = "2026-10-01"     # estoque pedido pela Entrega 3; a posição é o fim de 30/09
INICIO_HISTORICO = "2025-01-01"
# data de cada contagem (E5): só Pinheiros e o CD dizem; nas outras, 20/09 como a Pinheiros, e a
# alternativa (30/09, sem baixar vendas; na Batel até 13/09) dá a margem de erro
DATA_CONTAGEM = {"Pinheiros": "2026-09-20", "Eldorado": "2026-09-20", "Savassi": "2026-09-20", "Batel": "2026-09-20"}
DATA_ALTERNATIVA = {"Eldorado": ["2026-09-30"], "Savassi": ["2026-09-30"], "Batel": ["2026-09-13", "2026-09-30"]}
DATA_WMS = "2026-09-28"      # posição do WMS do CD; os movimentos de 28/09 já estão nela
UNIDADE_DA_CONTAGEM = {"Pinheiros": LOJAS_FISICAS["1"], "Eldorado": LOJAS_FISICAS["2"], "Savassi": LOJAS_FISICAS["3"],
                       "Batel": LOJAS_FISICAS["4"], "CD Cajamar": CD}
UNIDADE_PELO_NOME = {"pinheiros": LOJAS_FISICAS["1"], "eldorado": LOJAS_FISICAS["2"], "savassi": LOJAS_FISICAS["3"],
                     "batel": LOJAS_FISICAS["4"]}
EM_TRANSITO_DESDE = "2026-09-21"  # envio do CD dos últimos 10 dias sem recebimento fica em trânsito (E13)
DIAS_PAREAMENTO = 3               # saída do CD e recebimento da loja da mesma transferência (E4)

# letra da cor no SKU curto (C7) e a cor escrita por extenso no SKU com espaço
COR_DA_LETRA = {"B": "BRA", "G": "BEG", "O": "OFF", "C": "CIN", "Y": "AMA", "A": "AZM", "M": "MRR", "H": "VIN",
                "P": "PRE", "V": "OLI", "L": "LIL", "J": "JCL", "E": "JES"}
COR_DO_TEXTO = {"BEGE": "BEG", "OFF WHITE": "OFF", "OFF": "OFF", "VERDE OLIVA": "OLI", "VERDE": "OLI", "OLIVA": "OLI",
                "MARROM": "MRR", "PRETO": "PRE", "BRANCO": "BRA", "LILAS": "LIL", "CINZA MESCLA": "CIN", "CINZA": "CIN",
                "AZUL MARINHO": "AZM", "VINHO": "VIN", "AMARELO": "AMA", "JEANS CLARO": "JCL", "JEANS ESCURO": "JES"}
CORES = set(COR_DA_LETRA.values())
TAMANHOS = {"PP", "P", "M", "G", "GG", "U", "36", "38", "40", "42", "44", "46"}


def sem_acento(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()


# SKU oficial no formato longo VLT-CAT-NNN-COR-TAM (J2), a partir do longo, do curto (CAM004BM, cor
# pela letra da C7) ou do com espaço ("vlt cal 001 preto 38"). None quando não dá para converter
def sku_oficial(sku) -> str | None:
    if pd.isna(sku):
        return None
    s = sem_acento(str(sku)).upper().strip()
    longo = re.fullmatch(r"VLT-([A-Z]{3})-(\d{3})-([A-Z]{3})-([A-Z0-9]+)", s)
    if longo and longo[3] in CORES and longo[4] in TAMANHOS:
        return s
    curto = re.fullmatch(r"([A-Z]{3})(\d{3})([A-Z])([A-Z0-9]+)", s)
    if curto and curto[3] in COR_DA_LETRA and curto[4] in TAMANHOS:
        return f"VLT-{curto[1]}-{curto[2]}-{COR_DA_LETRA[curto[3]]}-{curto[4]}"
    espaco = re.fullmatch(r"VLT ([A-Z]{3}) (\d{3}) (.+) ([A-Z0-9]+)", s)
    if espaco and espaco[3] in COR_DO_TEXTO and espaco[4] in TAMANHOS:
        return f"VLT-{espaco[1]}-{espaco[2]}-{COR_DO_TEXTO[espaco[3]]}-{espaco[4]}"
    return None


# Saída do CD escrita em texto (E4): "2 un -> savassi", "transf p/ batel 3un", "enviado eldorado - 1 pçs",
# "TRANSF PINHEIROS 2". Devolve (unidade de destino, quantidade) ou None
def saida_do_cd(obs) -> tuple[str, int] | None:
    if pd.isna(obs):
        return None
    s = sem_acento(str(obs)).lower().strip()
    padroes = [r"(\d+) un -> (\w+)", r"transf p/ (\w+) (\d+)\s*un", r"enviado (\w+) - (\d+) pcs", r"transf (\w+) (\d+)"]
    for padrao in padroes:
        m = re.fullmatch(padrao, s)
        if m:
            numero, loja = (m[1], m[2]) if m[1].isdigit() else (m[2], m[1])
            if loja in UNIDADE_PELO_NOME:
                return UNIDADE_PELO_NOME[loja], int(numero)
    return None


# Divide o total contado por modelo entre as variantes, na proporção do peso de cada uma (E6), pelo
# maior resto: a soma das partes é sempre o total
def distribuir(total: int, pesos: dict) -> dict:
    soma = sum(pesos.values())
    if total == 0 or soma <= 0:
        return {}
    exatas = {k: total * p / soma for k, p in pesos.items()}
    partes = {k: int(v) for k, v in exatas.items()}
    for k in sorted(exatas, key=lambda k: exatas[k] - partes[k], reverse=True)[: total - sum(partes.values())]:
        partes[k] += 1
    return {k: v for k, v in partes.items() if v}


def dia_brasilia(criado_em: str) -> str:
    return datetime.fromisoformat(criado_em).astimezone(FUSO_BRASILIA).strftime("%Y-%m-%d")


# Posição contada de cada peça em cada unidade, com o SKU oficial. A Savassi contou por modelo e é
# distribuída pelas variantes pelo saldo positivo do sistema e, sem ele, pelas vendas (E6)
def posicao_contada(contagem: pd.DataFrame, estoque: pd.DataFrame, itens_loja: pd.DataFrame) -> pd.DataFrame:
    c = contagem.assign(q=pd.to_numeric(contagem["quantidade"]), unidade=contagem["unidade"].map(UNIDADE_DA_CONTAGEM))
    por_sku = c[c["nivel"] == "sku"].assign(sku=lambda d: d["codigo"].map(sku_oficial))
    # código digitado errado na contagem: a peça existe e não sai da conta (E10); fica com o código original
    por_sku["sku"] = por_sku["sku"].fillna("ILEGÍVEL " + por_sku["codigo"])
    linhas = [por_sku[["unidade", "sku", "q", "linha_origem", "observacao"]]]

    savassi = LOJAS_FISICAS["3"]
    sistema = estoque[(estoque["loja_id"] == "3")].assign(sku=estoque["sku"].map(sku_oficial), q=pd.to_numeric(estoque["qtd"]))
    sistema = sistema[sistema["q"] > 0].groupby("sku")["q"].sum()
    vendidas = itens_loja[itens_loja["unidade"] == savassi].groupby("sku")["q"].sum()
    for _, modelo in c[c["nivel"] == "modelo"].iterrows():
        prefixo = f"VLT-{modelo['codigo']}-"
        pesos = {k: v for k, v in sistema.items() if k.startswith(prefixo)} or \
                {k: v for k, v in vendidas.items() if isinstance(k, str) and k.startswith(prefixo)}
        partes = distribuir(int(modelo["q"]), pesos) or {f"{prefixo}SEM-VARIANTE": int(modelo["q"])}
        linhas.append(pd.DataFrame({"unidade": savassi, "sku": list(partes), "q": list(partes.values()),
                                    "linha_origem": modelo["linha_origem"], "observacao": "E6 distribuído por variante"}))
    return pd.concat(linhas, ignore_index=True)


# Itens vendidos que mexem no estoque: sem teste, cópia, cancelada (a saída e a volta se anulam, V17) e
# aguardando (nunca saiu). Online sai do CD; o ship from store, da Pinheiros (V10). Devolução volta
def itens_que_baixam(vendas: pd.DataFrame, itens: pd.DataFrame) -> pd.DataFrame:
    v = vendas[~vendas["teste"] & ~vendas["copia"].astype(bool) & ~vendas["situacao"].isin(["cancelado", "aguardando"])
               & ~vendas["relancamento_batel"]].copy()
    sfs = v["obs"].fillna("").str.lower().str.contains("sfs|pinheiros")
    v["unidade_estoque"] = v["unidade"].where(~sfs, LOJAS_FISICAS["1"])
    v["dia"] = v["criado_em"].map(dia_brasilia)
    it = itens.merge(v[["id", "unidade_estoque", "dia", "tipo", "canal"]], left_on="venda_id", right_on="id")
    sinal = it["tipo"].map({"devolucao": 1, "venda": -1, "troca": -1})
    return pd.DataFrame({"unidade": it["unidade_estoque"], "sku": it["sku"].map(sku_oficial), "dia": it["dia"],
                         "q": sinal * pd.to_numeric(it["qtd"]).abs(), "origem": "venda " + it["canal"]})


# Movimentos de entrada e transferência (E4): fornecedor no CD ou na loja; saída do CD pareada com o
# recebimento da loja (mesmo SKU e quantidade, até DIAS_PAREAMENTO dias); transferência entre lojas.
# Envio sem recebimento entra na loja na data do envio, menos os dos últimos dias, que ficam em trânsito (E13)
def movimentos_de_entrada(entradas: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    e = entradas.assign(sku=entradas["sku"].map(sku_oficial), q=pd.to_numeric(entradas["qtd"]), dia=entradas["data"])
    unidade = e["loja_id"].map(LOJAS_FISICAS).fillna(CD)
    obs = e["obs"].fillna("").map(lambda s: sem_acento(s).lower().strip())
    saida = e["obs"].map(saida_do_cd)
    eh_saida = saida.notna()
    de_loja = obs.str.extract(r"veio da (\w+)")[0].map(UNIDADE_PELO_NOME)
    do_cd = ~eh_saida & (unidade != CD) & (obs.str.contains("cd|transferencia") | (e["fornecedor"] == "CD"))

    movs = []
    # fornecedor: o que entra com quantidade e não é transferência
    fornecedor = e[~eh_saida & ~do_cd & de_loja.isna() & e["q"].notna()]
    movs.append(pd.DataFrame({"unidade": unidade[fornecedor.index], "sku": fornecedor["sku"], "dia": fornecedor["dia"],
                              "q": fornecedor["q"], "origem": "entrada de fornecedor"}))
    # entre lojas: entra numa e sai da outra no mesmo dia
    entre = e[de_loja.notna()]
    movs.append(pd.DataFrame({"unidade": unidade[entre.index], "sku": entre["sku"], "dia": entre["dia"],
                              "q": entre["q"], "origem": "transferência entre lojas"}))
    movs.append(pd.DataFrame({"unidade": de_loja[entre.index], "sku": entre["sku"], "dia": entre["dia"],
                              "q": -entre["q"], "origem": "transferência entre lojas"}))

    # CD → loja: pareia cada envio com um recebimento ainda livre, o mais próximo na data
    envios = e[eh_saida].assign(destino=saida[eh_saida].str[0], qtd_envio=saida[eh_saida].str[1])
    recebidos = e[do_cd].assign(destino=unidade[do_cd])
    livres = {chave: list(g.sort_values("dia").itertuples()) for chave, g in recebidos.groupby(["destino", "sku", "q"])}
    usados, transferencias, transito = set(), [], []
    for envio in envios.sort_values("dia").itertuples():
        candidatos = [r for r in livres.get((envio.destino, envio.sku, float(envio.qtd_envio)), []) if r.Index not in usados
                      and abs((pd.Timestamp(r.dia) - pd.Timestamp(envio.dia)).days) <= DIAS_PAREAMENTO]
        if candidatos:
            r = min(candidatos, key=lambda r: abs((pd.Timestamp(r.dia) - pd.Timestamp(envio.dia)).days))
            usados.add(r.Index)
            transferencias.append((envio.sku, envio.destino, envio.qtd_envio, envio.dia, r.dia, "pareada"))
        elif envio.dia >= EM_TRANSITO_DESDE:
            transito.append((envio.sku, envio.destino, envio.qtd_envio, envio.dia))
            transferencias.append((envio.sku, envio.destino, envio.qtd_envio, envio.dia, None, "em trânsito"))
        else:
            transferencias.append((envio.sku, envio.destino, envio.qtd_envio, envio.dia, envio.dia, "só o envio"))
    for r in recebidos[~recebidos.index.isin(usados)].itertuples():
        transferencias.append((r.sku, r.destino, r.q, r.dia, r.dia, "só o recebimento"))
    t = pd.DataFrame(transferencias, columns=["sku", "destino", "q", "dia_envio", "dia_recebimento", "forma"])
    movs.append(pd.DataFrame({"unidade": CD, "sku": t["sku"], "dia": t["dia_envio"], "q": -t["q"],
                              "origem": "transferência do CD para a loja (" + t["forma"] + ")"}))
    chegou = t[t["dia_recebimento"].notna()]
    movs.append(pd.DataFrame({"unidade": chegou["destino"], "sku": chegou["sku"], "dia": chegou["dia_recebimento"],
                              "q": chegou["q"], "origem": "transferência do CD para a loja (" + chegou["forma"] + ")"}))
    return pd.concat(movs, ignore_index=True), pd.DataFrame(transito, columns=["sku", "destino", "q", "dia_envio"])


# Posição de cada peça no fim de 30/09: a contada mais os movimentos depois da contagem. Nas lojas, a
# contagem é do começo do dia (as vendas do dia baixam, E5); no WMS, do fim do dia. Negativo vira zero
# (peça vendida que a contagem não viu) e entra na margem
def posicao_na_data_base(contada: pd.DataFrame, movimentos: pd.DataFrame, datas: dict) -> pd.DataFrame:
    linhas = []
    for unidade, data in datas.items():
        base = contada[contada["unidade"] == unidade].groupby("sku")["q"].sum()
        depois_da_contagem = (movimentos["dia"] > data) if unidade == CD else (movimentos["dia"] >= data)
        m = movimentos[(movimentos["unidade"] == unidade) & depois_da_contagem & (movimentos["dia"] < DATA_BASE)]
        pos = base.add(m.groupby("sku")["q"].sum(), fill_value=0)
        linhas.append(pd.DataFrame({"unidade": unidade, "sku": pos.index, "calculado": pos.values}))
    p = pd.concat(linhas, ignore_index=True)
    p["q"] = p["calculado"].clip(lower=0)
    return p


# Histórico de trás para frente (E2): parte da contagem e desfaz os movimentos dia a dia até jan/2025.
# Quando o saldo ficaria negativo, entrou mais do que ficou: faltou registrar uma saída (venda sem baixa,
# perda, transferência não anotada) e entra um ajuste de saída desse tamanho naquele dia. A entrada que
# faltou registrar não aparece como ajuste: vira saldo maior em 01/01/2025. Devolve (saldo por unidade e
# dia, ajustes de saída em peças)
def reconstruir_historico(contada: pd.DataFrame, movimentos: pd.DataFrame, datas: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    saldos_dia, ajustes = [], []
    dias = pd.date_range(INICIO_HISTORICO, "2026-09-30").strftime("%Y-%m-%d")
    for unidade, data in datas.items():
        base = contada[contada["unidade"] == unidade].groupby("sku")["q"].sum()
        m = movimentos[(movimentos["unidade"] == unidade) & movimentos["sku"].notna()]
        por_sku = {sku: g.groupby("dia")["q"].sum() for sku, g in m.groupby("sku")}
        total_dia = pd.Series(0.0, index=dias)
        for sku in set(base.index) | set(por_sku):
            mov = por_sku.get(sku, pd.Series(dtype=float, index=pd.Index([], dtype=str)))
            # fim do dia D = saldo; para trás, fim de D−1 = fim de D − movimentos de D
            depois = mov[(mov.index > data) if unidade == CD else (mov.index >= data)]
            saldo = max(0.0, float(base.get(sku, 0)) + depois.sum())  # o mesmo zero da posição em 30/09
            serie = {}
            for dia in reversed(dias):
                serie[dia] = saldo
                saldo -= mov.get(dia, 0.0)
                if saldo < 0:
                    ajustes.append((unidade, sku, dia, -saldo))
                    saldo = 0.0
            total_dia = total_dia.add(pd.Series(serie), fill_value=0)
        saldos_dia.append(pd.DataFrame({"unidade": unidade, "dia": total_dia.index, "pecas": total_dia.values}))
    return (pd.concat(saldos_dia, ignore_index=True),
            pd.DataFrame(ajustes, columns=["unidade", "sku", "dia", "q"]))


# Canal do estoque (ADR 0004): o CD é todo online e as lojas, loja física. Na Pinheiros, que despacha
# pedidos do site (E7), vai para o online a quantidade de cada peça já vendida por ship from store,
# limitada ao que existe; o total da loja não muda
def separar_canal(posicao: pd.DataFrame, vendidos: pd.DataFrame) -> pd.DataFrame:
    pinheiros = LOJAS_FISICAS["1"]
    sfs = vendidos[(vendidos["unidade"] == pinheiros) & vendidos["origem"].str.contains("site|app")]
    online_sfs = sfs.groupby("sku")["q"].sum().abs()
    p = posicao.assign(canal=posicao["unidade"].map(lambda u: "online" if u == CD else "loja_fisica"))
    eh_pinheiros = p["unidade"] == pinheiros
    online = p.loc[eh_pinheiros, "sku"].map(online_sfs).fillna(0).clip(upper=p.loc[eh_pinheiros, "q"])
    separado = p[eh_pinheiros & (online > 0)].assign(canal="online", q=online[online > 0])
    p.loc[eh_pinheiros, "q"] = p.loc[eh_pinheiros, "q"] - online
    return pd.concat([p[p["q"] > 0], separado], ignore_index=True)


def calcular_estoque(origem: Path, destino: Path, vendas_classificadas: pd.DataFrame):
    contagem = pd.read_csv(origem / "contagem_set26.csv", dtype=str)
    estoque = pd.read_csv(origem / "banco_estoque.csv", dtype=str)
    entradas = pd.read_csv(origem / "banco_entradas_estoque.csv", dtype=str)
    itens = pd.read_csv(origem / "banco_itens_venda.csv", dtype=str)
    catalogo = pd.read_csv(origem / "catalogo_produtos.csv", dtype=str)

    vendidos = itens_que_baixam(vendas_classificadas, itens)
    entrou, transito = movimentos_de_entrada(entradas)
    movimentos = pd.concat([vendidos, entrou], ignore_index=True)
    sem_sku = movimentos["sku"].isna()
    movimentos = movimentos[~sem_sku]
    contada = posicao_contada(contagem, estoque, vendidos.assign(q=vendidos["q"].abs()))

    datas = {**{UNIDADE_DA_CONTAGEM[k]: v for k, v in DATA_CONTAGEM.items()}, CD: DATA_WMS}
    posicao = posicao_na_data_base(contada, movimentos, datas)

    # margem da data da contagem (E5): a mesma conta com a data alternativa de cada loja
    faixas = []
    for nome, alternativas in DATA_ALTERNATIVA.items():
        unidade = UNIDADE_DA_CONTAGEM[nome]
        principal = posicao.loc[posicao["unidade"] == unidade, "q"].sum()
        for data in alternativas:
            alt = posicao_na_data_base(contada, movimentos, {unidade: data})["q"].sum()
            faixas.append((unidade, f"E5 contagem em {data[8:]}/{data[5:7]}", alt - principal))
    margem = pd.DataFrame(faixas, columns=["unidade", "motivo", "pecas"])
    batel = contagem[(contagem["unidade"] == "Batel") & contagem["observacao"].fillna("").str.lower()
                     .str.contains("faltou|recontar")]
    margem.loc[len(margem)] = [LOJAS_FISICAS["4"], f"E11 {len(batel)} linhas para recontar ou sem o provador",
                               pd.to_numeric(batel["quantidade"]).sum()]
    sistema_eldorado = estoque[estoque["loja_id"] == "2"].assign(sku=estoque["sku"].map(sku_oficial), q=pd.to_numeric(estoque["qtd"]))
    sistema_eldorado = sistema_eldorado[sistema_eldorado["q"] > 0].groupby("sku")["q"].sum()
    nao_contadas = sistema_eldorado[~sistema_eldorado.index.isin(contada.loc[contada["unidade"] == LOJAS_FISICAS["2"], "sku"])]
    margem.loc[len(margem)] = [LOJAS_FISICAS["2"], f"E12 {len(nao_contadas)} peças com saldo no sistema e fora da contagem",
                               nao_contadas.sum()]
    ilegiveis = contada[contada["sku"].str.startswith("ILEGÍVEL")]
    margem.loc[len(margem)] = ["todas", f"E10 {len(ilegiveis)} códigos ilegíveis na contagem (contam no total, sem variante)",
                               ilegiveis["q"].sum()]
    margem.loc[len(margem)] = ["todas", "vendidas depois da contagem sem peça contada (posição negativa, vira zero)",
                               (posicao["q"] - posicao["calculado"]).sum()]

    # nome pela variante e, quando ela não está no catálogo (C4, E10), pelo modelo
    modelo = catalogo["sku"].map(lambda s: (lambda k: f"VLT-{k[:3]}-{k[3:6]}" if k else None)(chave_sku(s)))
    nomes_modelo = catalogo.assign(modelo=modelo).dropna(subset=["modelo"]).groupby("modelo")["nome"].last()
    nomes = catalogo.assign(sku=catalogo["sku"].map(sku_oficial)).dropna(subset=["sku"]).groupby("sku")["nome"].last()
    por_produto = separar_canal(posicao[posicao["q"] > 0], vendidos)
    por_produto["produto"] = por_produto["sku"].map(nomes).fillna(por_produto["sku"].str[:11].map(nomes_modelo))
    gravar(por_produto[["unidade", "sku", "produto", "canal", "q"]].sort_values(["unidade", "sku", "canal"]),
           destino, "estoque_por_produto")
    por_unidade = posicao.groupby("unidade")["q"].sum().to_frame("pecas")
    por_unidade.loc["Em trânsito do CD para a loja (E13)"] = transito["q"].sum()
    por_unidade.loc["total"] = por_unidade["pecas"].sum()
    gravar(por_unidade.reset_index(), destino, "estoque_por_unidade")
    gravar(margem, destino, "estoque_margem")

    historico, ajustes = reconstruir_historico(contada, movimentos, datas)
    gravar(historico.pivot(index="dia", columns="unidade", values="pecas").reset_index(), destino, "estoque_historico_diario")
    resumo_ajustes = ajustes.assign(mes=ajustes["dia"].str[:7]).pivot_table(index="mes", columns="unidade", values="q",
                                                                            aggfunc="sum", fill_value=0)
    gravar(resumo_ajustes.reset_index(), destino, "estoque_ajustes_por_mes")
    movimentos_por_origem = movimentos.groupby("origem")["q"].agg(["count", "sum"]).reset_index()
    gravar(movimentos_por_origem, destino, "estoque_movimentos_por_origem")
    print(por_unidade.to_string())
    print(margem.to_string(index=False))
    print("movimentos sem SKU convertível:", int(sem_sku.sum()))
    print("ajustes do histórico:", len(ajustes), "linhas,", ajustes["q"].sum(), "peças; por unidade:",
          ajustes.groupby("unidade")["q"].sum().to_dict())
    print(movimentos_por_origem.to_string(index=False))


def gravar(df: pd.DataFrame, destino: Path, nome: str):
    df.to_csv(destino / f"{nome}.csv", index=False, encoding="utf-8")


# ---------- execução ----------

def main():
    parser = argparse.ArgumentParser(description="Calcula os três números da Vulto a partir do staging.")
    parser.add_argument("--origem", type=Path, default=Path("staging_vulto"), help="pasta do staging (etapa 1)")
    parser.add_argument("--destino", type=Path, default=Path("numeros_vulto"), help="pasta dos números")
    args = parser.parse_args()
    args.destino.mkdir(parents=True, exist_ok=True)
    vendas = calcular_faturamento(args.origem, args.destino)
    calcular_passivo(args.origem, args.destino, vendas)
    calcular_estoque(args.origem, args.destino, vendas)


if __name__ == "__main__":
    main()
