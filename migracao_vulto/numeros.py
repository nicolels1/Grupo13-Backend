# Calcula os três números da Entrega 3 a partir do staging (etapa 1), aplicando as decisões do
# registro (registro_decisoes.md). As regras de cada decisão estão em regras.py; aqui ficam a soma, o
# agrupamento e a gravação. Grava só agregados (por mês, loja, canal, produto), sem CPF nem e-mail, em --destino.
# Uso, na raiz do repositório com o venv ativo:
#   python -m migracao_vulto.numeros --origem staging_vulto --destino numeros_vulto
import argparse
from datetime import datetime
from pathlib import Path

import pandas as pd

from migracao_vulto.regras import (
    CD, CPF_TESTE, DATA_ALTERNATIVA, DATA_CONTAGEM, DATA_SALDO_INICIAL, DATA_WMS, FIM, FUSO_BRASILIA, INICIO,
    LOJAS_FISICAS, UNIDADE_DA_CONTAGEM, VALOR_PONTO, chave_sku, creditos_a_estornar, creditos_sem_dobro,
    dono_dos_pontos, epoca, Identidades, itens_que_baixam, lancamentos_banco, lancamentos_batel,
    movimentos_de_entrada, posicao_contada, posicao_na_data_base, reconstruir_historico,
    resgates_so_na_planilha, saldo_inicial, separar_canal, sku_oficial, so_digitos, valorar_fifo,
)


# ---------- faturamento ----------

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
