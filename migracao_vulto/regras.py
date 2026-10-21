# Regras das decisões da migração da Vulto (registro_decisoes.md), usadas pelos três números
# (numeros.py) e pelo tratamento (tratar.py): como cada linha do staging é lida, classificada e
# corrigida. Cada regra cita a decisão que a define (V1, K3, E5...). Aqui não se grava nada.
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

# Tabelas de tradução da Vulto para a Lorenzi (de_para/*.csv). Cada linha traz a decisão que a justifica;
# valor que não está na tabela não é traduzido no chute
DE_PARA = Path(__file__).parent / "de_para"


def de_para(nome: str) -> pd.DataFrame:
    return pd.read_csv(DE_PARA / f"{nome}.csv", dtype=str, keep_default_na=False)


_UNIDADES = de_para("unidade").set_index("loja_id")

FUSO_BRASILIA = ZoneInfo("America/Sao_Paulo")
INICIO, FIM = "2025-01", "2026-09"  # período do faturamento pedido pela Entrega 3

LOJAS_FISICAS = _UNIDADES.loc[_UNIDADES["tipo"] == "loja", "unidade"].to_dict()  # loja_id → nome (U1)
CD = _UNIDADES.loc["7", "unidade"]  # site e app vendem do CD (V12)
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


# Coleções do catálogo, da mais antiga para a mais nova, com a data em que o preço de cada uma passou a
# valer no histórico de preço (C14, premissa). A aba antiga tem os preços de 2024; a Atemporais é revisada
# em março (o reajuste das vendas começa em mar/2025, C8); a Inverno 25 recebe e vende a partir do fim de
# mar/2025; a Verão 26 e a Inverno 26 valem a partir da primeira data de lançamento da planilha; a Verão 25,
# anterior às vendas, fica com o mesmo dia da Verão 26 um ano antes. A ordem decide a coleção mais nova
# (C1): nos 43 SKUs da Atemporais e da Verão 26, as lojas cobraram o da Verão 26 em 1.176 de 1.228 vendas
ABA_ANTIGA = "2024 (aba antiga)"
INICIO_COLECAO = {ABA_ANTIGA: "2024-01-01", "Verão 25": "2024-09-25", "Atemporais": "2025-03-01",
                  "Inverno 25": "2025-03-25", "Verão 26": "2025-09-25", "Inverno 26": "2026-03-25"}
# preço praticado nas lojas (C19) e "os últimos 3 meses" da C8: jul–set/2026; a C19 exige 3 vendas
PRATICADO_DESDE, PRATICADO_ATE, VENDAS_DO_PRATICADO = "2026-07-01", "2026-10-01", 3
SUFIXO_PECA_ANTIGA = "-A"  # SKU da peça antiga de um código reaproveitado (C2)


# SKU sem o tamanho, com o tamanho só na coluna (C20): a grade inteira ("P/M/G/GG") vira uma linha por
# tamanho e o tamanho único ("U") completa o SKU. Guarda o SKU escrito na planilha em sku_vulto
def expandir_grade(catalogo: pd.DataFrame) -> pd.DataFrame:
    c = catalogo.assign(sku_vulto=catalogo["sku"], grade=False)
    sem_tamanho = c["sku"].map(sku_oficial).isna() & (c["tamanho"].fillna("") != "")
    grade = c[sem_tamanho].assign(tamanho=c.loc[sem_tamanho, "tamanho"].str.split("/")).explode("tamanho")
    grade = grade.assign(tamanho=grade["tamanho"].str.strip(), grade=True)
    grade["sku"] = grade["sku"] + " " + grade["tamanho"]
    return pd.concat([c[~sem_tamanho], grade], ignore_index=True)


def digito_ean(base: str) -> str:
    return str((10 - sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(base)) % 10) % 10)


# EAN de fábrica que dá para usar (C12): 13 dígitos com o dígito verificador certo. O Excel cortou alguns
# em notação científica (7890231000000): os zeros do fim não são o código, mesmo quando o dígito bate por acaso
def ean_de_fabrica(ean) -> bool:
    s = "" if pd.isna(ean) else str(ean).strip()
    return bool(re.fullmatch(r"\d{13}", s)) and not s.endswith("000000") and s[12] == digito_ean(s[:12])


def nome_normalizado(nome) -> str:
    return re.sub(r"\s+", " ", sem_acento(str(nome)).upper().strip())


# Código reaproveitado (C2): modelo cujo nome na aba antiga não aparece em nenhuma aba nova. Devolve
# {modelo: nome da peça antiga}; as linhas da aba antiga desse modelo são da peça antiga
def codigos_reaproveitados(catalogo: pd.DataFrame) -> dict:
    c = catalogo.assign(modelo=catalogo["sku"].map(sku_oficial).str[:11], nome_n=catalogo["nome"].map(nome_normalizado))
    c = c.dropna(subset=["modelo"])
    antiga = c[c["colecao"] == ABA_ANTIGA].groupby("modelo")["nome_n"].agg(set)
    novas = c[c["colecao"] != ABA_ANTIGA].groupby("modelo")["nome_n"].agg(set)
    return {m: min(nomes) for m, nomes in antiga.items() if m in novas and not nomes & novas[m]}


# Variante do item vendido: o SKU oficial e, no código reaproveitado, a peça pela descrição gravada no
# item ("Moletom Careca Estonado MRR" é a peça antiga do VLT-MOL-001), nunca pela data (C2)
def variante_vendida(sku_of, descricao, reaproveitados: dict):
    if sku_of is None or pd.isna(sku_of):
        return None
    antigo = reaproveitados.get(sku_of[:11])
    if antigo and nome_normalizado(descricao).startswith(antigo):
        return sku_of + SUFIXO_PECA_ANTIGA
    return sku_of


# Itens vendidos em loja física, com a variante e o preço cobrado, para o preço praticado (C8, C19):
# só venda (sem devolução), sem teste e sem os pedidos do app (que podem estar em centavos, V1)
def vendas_em_loja(vendas: pd.DataFrame, itens: pd.DataFrame, reaproveitados: dict) -> pd.DataFrame:
    v = vendas[vendas["loja_id"].astype(str).isin(LOJAS_FISICAS)]
    v = v[[not eh_teste(*r) for r in zip(v["obs"], v["cliente_ref"], v["cliente_valor"], v["pedido_externo"])]]
    v = v[~v["pedido_externo"].fillna("").str.startswith("APP-")]
    it = itens.merge(v[["id", "criado_em"]], left_on="venda_id", right_on="id")
    it = it.assign(q=pd.to_numeric(it["qtd"]), preco=pd.to_numeric(it["preco_unit"]),
                   dia=it["criado_em"].map(dia_brasilia), sku_of=it["sku"].map(sku_oficial))
    it = it[(it["q"] > 0) & (it["preco"] > 0) & it["sku_of"].notna()]
    it["variante"] = [variante_vendida(s, d, reaproveitados) for s, d in zip(it["sku_of"], it["descricao"])]
    return it[["variante", "dia", "preco", "q"]]


# Preço mais cobrado de cada variante numa janela (o de mais peças; no empate, o maior) e quantas vendas
def preco_mais_cobrado(vendidos: pd.DataFrame, desde: str, ate: str) -> pd.DataFrame:
    j = vendidos[(vendidos["dia"] >= desde) & (vendidos["dia"] < ate)]
    pecas = j.groupby(["variante", "preco"])["q"].sum().reset_index().sort_values(["variante", "q", "preco"])
    mais = pecas.groupby("variante").tail(1).set_index("variante")
    return mais.assign(vendas=j.groupby("variante").size())[["preco", "vendas"]]


# Primeiro dia em que a loja física cobrou esse preço pela variante, a partir de uma data (data do preço
# praticado no histórico, C19 e C8)
def primeira_venda_ao_preco(vendidos: pd.DataFrame, variante: str, preco: float, desde: str) -> str | None:
    d = vendidos.loc[(vendidos["variante"] == variante) & (vendidos["preco"].round(2) == round(preco, 2))
                     & (vendidos["dia"] >= desde), "dia"]
    return d.min() if len(d) else None


# ---------- vendas: classificação ----------

# Situação do pedido a partir das grafias do status (V13). Vazio só existe na loja física em
# jan–fev/2025 e conta como pago (V2)
_SITUACAO = dict(zip(de_para("status_venda")["valor_vulto"], de_para("status_venda")["situacao"]))


def situacao(status) -> str:
    s = "" if pd.isna(status) else str(status).strip().lower()
    s = s or "(vazio)"
    if s in _SITUACAO:
        return _SITUACAO[s]
    raise ValueError(f"status desconhecido: {status!r} (falta em de_para/status_venda.csv)")


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


# ---------- vendas: faturamento de cada venda ----------

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


# ---------- passivo de pontos ----------

DATA_SALDO_INICIAL = "2025-01-01"  # a coluna Pontos da planilha foi congelada nesta data (K5a)
CORTE_VALOR_PONTO = "2025-03-01"   # ponto ganho antes vale R$ 0,10; a partir daqui, R$ 0,05 (K3)
VALOR_PONTO = {"antigo": 0.10, "novo": 0.05}
CPF_TESTE = "12345678909"          # CPF de teste: não migra, nem os pontos (K8, V8)
RESGATE_MINIMO = 100               # regra do Vulto+; abaixo disso, "2.000" foi lido como 2 (K7b)
DDDS = {11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 24, 27, 28, 31, 32, 33, 34, 35, 37, 38, 41, 42, 43, 44, 45, 46,
        47, 48, 49, 51, 53, 54, 55, 61, 62, 63, 64, 65, 66, 67, 68, 69, 71, 73, 74, 75, 77, 79, 81, 82, 83, 84, 85,
        86, 87, 88, 89, 91, 92, 93, 94, 95, 96, 97, 98, 99}
LOJA_DO_RESGATE = {_UNIDADES.loc[i, "nome_curto"]: i for i in LOJAS_FISICAS}  # "Pinheiros" → "1"


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


# ---------- estoque ----------

DATA_BASE = "2026-10-01"     # estoque pedido pela Entrega 3; a posição é o fim de 30/09
INICIO_HISTORICO = "2025-01-01"
# data de cada contagem (E5): só Pinheiros e o CD dizem; nas outras, 20/09 como a Pinheiros, e a
# alternativa (30/09, sem baixar vendas; na Batel até 13/09) dá a margem de erro
DATA_CONTAGEM = {"Pinheiros": "2026-09-20", "Eldorado": "2026-09-20", "Savassi": "2026-09-20", "Batel": "2026-09-20"}
DATA_ALTERNATIVA = {"Eldorado": ["2026-09-30"], "Savassi": ["2026-09-30"], "Batel": ["2026-09-13", "2026-09-30"]}
DATA_WMS = "2026-09-28"      # posição do WMS do CD; os movimentos de 28/09 já estão nela
UNIDADE_DA_CONTAGEM = {**{_UNIDADES.loc[i, "nome_curto"]: LOJAS_FISICAS[i] for i in LOJAS_FISICAS},
                       _UNIDADES.loc["7", "nome_curto"]: CD}  # nome da aba da contagem → unidade
UNIDADE_PELO_NOME = {_UNIDADES.loc[i, "nome_curto"].lower(): LOJAS_FISICAS[i] for i in LOJAS_FISICAS}  # "pinheiros"
EM_TRANSITO_DESDE = "2026-09-21"  # envio do CD dos últimos 10 dias sem recebimento fica em trânsito (E13)
DIAS_PAREAMENTO = 3               # saída do CD e recebimento da loja da mesma transferência (E4)

# letra da cor no SKU curto (C7) e a cor escrita por extenso no SKU com espaço
COR_DA_LETRA = dict(zip(de_para("cor")["letra_curta"], de_para("cor")["codigo"]))
COR_DO_TEXTO = dict(zip(de_para("cor_texto")["texto_vulto"], de_para("cor_texto")["codigo"]))
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


# Estoque na data base (E2, E4, E5, E10, E13): baixas das vendas, entradas e transferências, a posição
# contada e a posição de cada peça no fim de 30/09. Usado pelos números (soma) e pelo tratamento (a peça
# descontinuada com estoque fica à venda, C3). Devolve as peças que baixam, os movimentos com SKU (e quantos
# ficaram sem), a posição contada, a posição em 30/09, o que está em trânsito e a data da contagem de cada unidade
def estoque_na_data_base(vendas_classificadas: pd.DataFrame, itens: pd.DataFrame, entradas: pd.DataFrame,
                         contagem: pd.DataFrame, estoque: pd.DataFrame) -> dict:
    vendidos = itens_que_baixam(vendas_classificadas, itens)
    entrou, transito = movimentos_de_entrada(entradas)
    movimentos = pd.concat([vendidos, entrou], ignore_index=True)
    sem_sku = int(movimentos["sku"].isna().sum())
    movimentos = movimentos[movimentos["sku"].notna()]
    contada = posicao_contada(contagem, estoque, vendidos.assign(q=vendidos["q"].abs()))
    datas = {**{UNIDADE_DA_CONTAGEM[k]: v for k, v in DATA_CONTAGEM.items()}, CD: DATA_WMS}
    return dict(vendidos=vendidos, movimentos=movimentos, sem_sku=sem_sku, contada=contada, transito=transito,
                datas=datas, posicao=posicao_na_data_base(contada, movimentos, datas))


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
