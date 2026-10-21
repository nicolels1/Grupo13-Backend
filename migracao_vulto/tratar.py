# Tratamento dos dados da Vulto (etapa 6): aplica as decisões do registro (registro_decisoes.md) ao
# staging (etapa 1) e grava, em --destino, um CSV por tabela da plataforma, já no formato da carga (etapa 7).
# As regras estão em regras.py e as traduções em de_para/; aqui só se monta cada tabela.
# Os ids da Lorenzi nascem no banco, então cada registro sai com a sua chave natural (nome da unidade,
# e-mail da conta...), que a carga troca pelo id. Junto saem:
#   origem_migracao.csv: as linhas da tabela origem_migracao (P4), de onde veio cada registro e o valor
#                        original de cada campo que a migração mudou, com a decisão que mudou. O
#                        id_registro sai com a chave natural, e a carga grava o id do banco no lugar:
#                        a plataforma acha o registro migrado pelo id (V13, migracao_repository.migrado)
#   pendencias.csv:      o que não se resolveu (valor sem tradução, ligação que não fecha); nada é chutado
# O destino tem CPF, e-mail e telefone: fica fora do git (.gitignore).
# Uso, na raiz do repositório com o venv ativo:
#   python -m migracao_vulto.tratar --origem staging_vulto --destino tratado_vulto
import argparse
from pathlib import Path

import pandas as pd

from migracao_vulto import regras as r
from migracao_vulto.regras import de_para

COLUNAS_ORIGEM = ["tabela", "id_registro", "fonte", "linha_origem", "campo", "valor_original", "decisao"]
COLUNAS_PENDENCIA = ["tabela", "fonte", "linha_origem", "campo", "valor", "motivo"]
FONTE_BANCO = "vulto.db"
FONTE_DE_PARA = "de_para"  # registro que nasce de uma tabela de_para, sem linha no staging


class Saida:
    """Junta as tabelas tratadas, a origem de cada registro e as pendências, e grava no fim."""

    def __init__(self):
        self.tabelas: dict[str, pd.DataFrame] = {}
        self.origem: list[dict] = []
        self.pendencias: list[dict] = []

    def tabela(self, nome: str, df: pd.DataFrame):
        self.tabelas[nome] = df

    # linha de origem do registro (campo vazio) ou de um campo que a migração mudou (com o valor original)
    def de_onde(self, tabela, id_registro, fonte, linha_origem, decisao, campo=None, valor_original=None):
        self.origem.append(dict(tabela=tabela, id_registro=id_registro, fonte=fonte, linha_origem=linha_origem,
                                campo=campo, valor_original=valor_original, decisao=decisao))

    def pendente(self, tabela, fonte, linha_origem, campo, valor, motivo):
        self.pendencias.append(dict(tabela=tabela, fonte=fonte, linha_origem=linha_origem, campo=campo,
                                    valor=valor, motivo=motivo))

    def gravar(self, destino: Path):
        destino.mkdir(parents=True, exist_ok=True)
        for nome, df in self.tabelas.items():
            df.to_csv(destino / f"{nome}.csv", index=False, encoding="utf-8")
        pd.DataFrame(self.origem, columns=COLUNAS_ORIGEM).to_csv(destino / "origem_migracao.csv", index=False,
                                                                 encoding="utf-8")
        pd.DataFrame(self.pendencias, columns=COLUNAS_PENDENCIA).to_csv(destino / "pendencias.csv", index=False,
                                                                        encoding="utf-8")


def ler(origem: Path, nome: str) -> pd.DataFrame:
    return pd.read_csv(origem / f"{nome}.csv", dtype=str, keep_default_na=False)


# ---------- unidades e contas internas ----------

# U1: as 4 lojas e o CD, com o endereço que se deduz do nome e da cidade; o resto fica "A confirmar".
# V12: o site (5) e o app (6) não são unidades, as vendas deles saem do CD, então só viram origem do CD
def tratar_unidades(origem: Path, saida: Saida):
    lojas = ler(origem, "banco_lojas").set_index("id")
    mapa = de_para("unidade")
    linhas = []
    for u in mapa.itertuples():
        loja = lojas.loc[u.loja_id]
        if u.unidade not in {l["nome"] for l in linhas}:
            # a cidade do CD vem da linha dele (7); as do site e do app são vazias
            cidade = lojas.loc[mapa.loc[mapa["unidade"] == u.unidade, "loja_id"], "cidade"].max()
            linhas.append(dict(nome=u.unidade, tipo=u.tipo, marca="vulto", despacha_online=u.despacha_online == "sim",
                               rua="A confirmar", numero="s/n", complemento=None, bairro=u.bairro, cidade=cidade,
                               uf=u.uf, cep="00000000", ativo=True))
        saida.de_onde("unidade", u.unidade, FONTE_BANCO, loja["linha_origem"], u.decisao)
        if loja["nome"] != u.unidade:
            saida.de_onde("unidade", u.unidade, FONTE_BANCO, loja["linha_origem"], u.decisao, "nome", loja["nome"])
    saida.tabela("unidade", pd.DataFrame(linhas))


# P9: os 13 vendedores, as 3 do SAC e a "Migração Vulto" viram conta interna inativa, com o e-mail fictício
# @vulto.example. A conta não vem do Supabase Auth: a carga cria o usuário dela lá (por isso sem id aqui)
def tratar_contas_internas(origem: Path, saida: Saida):
    funcionarios = ler(origem, "banco_funcionarios").set_index("id")
    unidades = de_para("unidade").set_index("loja_id")["unidade"]
    linhas = []
    for c in de_para("conta_interna").itertuples():
        linhas.append(dict(email=c.email, nome=c.nome, cpf=None, tipo_conta="interna", status_conta="inativa",
                           modelo_acesso=c.modelo, unidade=unidades[c.loja_id] if c.loja_id else None))
        if c.funcionario_vulto:
            f = funcionarios.loc[c.funcionario_vulto]
            saida.de_onde("usuario", c.email, FONTE_BANCO, f["linha_origem"], c.decisao)
        else:  # SAC e "Migração Vulto" não estão no cadastro de funcionários
            saida.de_onde("usuario", c.email, FONTE_DE_PARA, f"conta_interna#{c.conta}", c.decisao)
    saida.tabela("usuario", pd.DataFrame(linhas))


# ---------- catálogo ----------

FONTE_CATALOGO = "catalogo_vulto_2026_REVISADO_final.xlsx"
FONTE_CONTAGEM = "contagem_inventario_set26.xlsx"
# P8: o item vendido sem a peça identificada (DIVERSO, PRODUTO SEM CADASTRO) aponta para esta variante
SKU_SEM_CADASTRO = "VLT-SEM-CADASTRO"
NOME_SEM_CADASTRO = "Item sem cadastro Vulto"
CATEGORIA_SEM_CADASTRO = "Sem cadastro Vulto"
# C16: a mesma imagem da marca em todo produto, no bucket de fotos de produto; a carga sobe o arquivo
IMAGEM_GENERICA = "vulto/sem-foto.png"


# Estoque de cada variante em 01/10/2026 (posição em 30/09 mais o que está em trânsito, E13). No código
# reaproveitado, o SKU que as duas peças usam fica com a nova e o que só a antiga tem fica com ela (C2).
# Devolve também a contagem lida, de onde vem a variante criada a partir do modelo (E10)
def estoque_por_variante(origem: Path, variantes: set) -> tuple[pd.Series, pd.DataFrame]:
    vendas, itens = ler(origem, "banco_vendas"), ler(origem, "banco_itens_venda")
    lancamentos = r.lancamentos_banco(vendas.replace("", None), itens.replace("", None),
                                      ler(origem, "catalogo_produtos").replace("", None))
    e = r.estoque_na_data_base(lancamentos[lancamentos["linha_origem"].astype(str).str.startswith("vendas#")],
                               itens.replace("", None), ler(origem, "banco_entradas_estoque").replace("", None),
                               ler(origem, "contagem_set26").replace("", None), ler(origem, "banco_estoque").replace("", None),
                               ler(origem, "catalogo_produtos"))
    q = pd.concat([e["posicao"][["sku", "q"]], e["transito"][["sku", "q"]]]).groupby("sku")["q"].sum()
    antiga = {s + r.SUFIXO_PECA_ANTIGA for s in q.index} & variantes
    dono = {s: s + r.SUFIXO_PECA_ANTIGA if s not in variantes and s + r.SUFIXO_PECA_ANTIGA in antiga else s
            for s in q.index}
    return q.groupby(dono).sum(), e["contada"]


# Histórico de preço de uma variante: os preços em ordem de data, sem repetir o mesmo preço seguido.
# O último é o preço da variante
def historico(precos: list[tuple[str, float, str]]) -> list[dict]:
    linhas, anterior = [], None
    for data, preco, decisao in sorted(precos, key=lambda p: p[0]):
        if anterior is None or round(preco, 2) != round(anterior, 2):
            linhas.append(dict(preco_anterior=anterior, preco_novo=round(preco, 2), alterado_em=data, decisao=decisao))
            anterior = preco
    return linhas


# J2, C1–C20: uma variante por SKU oficial (o curto e o com espaço da mesma peça se juntam, C13), agrupadas
# em produtos pelo código do modelo (C6), com o preço da coleção mais nova e o histórico das outras
def tratar_catalogo(origem: Path, saida: Saida):
    bruto = ler(origem, "catalogo_produtos")
    reaproveitados = r.codigos_reaproveitados(bruto)
    c = r.expandir_grade(bruto)
    c["sku_of"] = c["sku"].map(r.sku_oficial)
    c["modelo"] = c["sku_of"].str[:11]
    c["peca_antiga"] = c["modelo"].isin(reaproveitados) & (c["colecao"] == r.ABA_ANTIGA)
    sufixo = c["peca_antiga"].map({True: r.SUFIXO_PECA_ANTIGA, False: ""})
    c["variante"], c["produto"] = c["sku_of"] + sufixo, c["modelo"] + sufixo
    c["data"] = c["colecao"].map(r.INICIO_COLECAO)
    c["preco_n"], c["custo_n"] = pd.to_numeric(c["preco"]), pd.to_numeric(c["custo"])
    # C17: custo maior que o preço e igual ao preço de outra linha do mesmo modelo é coluna trocada. O custo
    # maior sem esse par (14112.00 num preço de 299.90) é custo sem a vírgula: o preço fica, e o custo não
    # migra (C11)
    precos_do_modelo = c.groupby("produto")["preco_n"].agg(lambda p: set(p.round(2)))
    trocado = (c["custo_n"] > c["preco_n"]) & [round(custo, 2) in precos_do_modelo[p] for custo, p
                                               in zip(c["custo_n"].fillna(-1), c["produto"])]
    c.loc[trocado, ["preco_n", "custo_n"]] = c.loc[trocado, ["custo_n", "preco_n"]].values
    c = c.sort_values(["data", "linha_origem"])

    vendidos = r.vendas_em_loja(ler(origem, "banco_vendas"), ler(origem, "banco_itens_venda"), reaproveitados)
    praticado = r.preco_mais_cobrado(vendidos, r.PRATICADO_DESDE, r.PRATICADO_ATE)
    estoque, contada = estoque_por_variante(origem, set(c["variante"]))
    nome_cor = de_para("cor").set_index("codigo")["nome"]

    variantes, historicos, para_marina = [], [], []
    for v, linhas in c.groupby("variante", sort=False):
        nova = linhas.iloc[-1]
        precos = [(l.data, l.preco_n, "C1") for l in linhas.itertuples()]
        if (linhas["colecao"] == r.ABA_ANTIGA).all() and not nova.peca_antiga:
            # C8: só na aba antiga; vale o preço do mesmo modelo na coleção mais nova em que ele está
            modelo = c[(c["produto"] == nova.produto) & (c["colecao"] != r.ABA_ANTIGA)]
            if len(modelo):
                ultima = modelo[modelo["data"] == modelo["data"].max()]
                precos.append((ultima["data"].iloc[0], ultima["preco_n"].mode().max(), "C8"))
            elif v in praticado.index:
                p = praticado.loc[v, "preco"]
                precos.append((r.primeira_venda_ao_preco(vendidos, v, p, nova.data), p, "C8"))
            # sem o modelo em outra aba e sem venda em jul–set/2026, fica o preço da aba antiga: é o que as
            # lojas cobram (nos 8 modelos, o mais cobrado desde mar/2025)
        escolhido = sorted(precos, key=lambda p: p[0])[-1]
        # C19: o preço mais cobrado nas lojas em jul–set/2026, se for maior que o escolhido
        if v in praticado.index and praticado.loc[v, "vendas"] >= r.VENDAS_DO_PRATICADO \
                and praticado.loc[v, "preco"] > escolhido[1] + 0.005:
            p = praticado.loc[v, "preco"]
            precos.append((r.primeira_venda_ao_preco(vendidos, v, p, escolhido[0]), p, "C19"))
        hist = historico(precos)
        preco = hist[-1]["preco_novo"]
        historicos += [dict(sku=v, **h) for h in hist]

        # C3: descontinuada pela marcação da coleção mais nova (linha toda ou só o SKU em vermelho); a peça
        # antiga de um código reaproveitado saiu de linha. Com estoque, segue à venda até acabar
        descontinuada = nova.peca_antiga or nova.descontinuado in ("True", "")
        q = int(estoque.get(v, 0))
        eans = [l.ean for l in linhas.iloc[::-1].itertuples() if r.ean_de_fabrica(l.ean)]
        _, cat, num, cor, tam = nova.sku_of.split("-")
        variantes.append(dict(sku=v, produto=nova.produto, ean=eans[0] if eans else None, cor=nome_cor[cor],
                              tamanho=tam, preco=preco, ativo=not descontinuada or q > 0, estoque_01_10=q))

        # origem de cada linha da planilha e o valor original do que mudou
        for l in linhas.itertuples():
            de_onde = lambda campo=None, valor=None, decisao="J2": saida.de_onde(
                "variante", v, FONTE_CATALOGO, l.linha_origem, decisao, campo, valor)
            de_onde(decisao="C20" if l.grade else ("C13" if len(linhas) > 1 else "J2"))
            if l.sku_vulto != v:
                de_onde("sku", l.sku_vulto, "C2" if l.peca_antiga else ("C20" if l.grade else "J2"))
            if round(l.preco_n, 2) != preco or l.preco_n != pd.to_numeric(l.preco):
                de_onde("preco", l.preco, "C17" if l.preco_n != pd.to_numeric(l.preco) else
                        ("C8" if l.colecao == r.ABA_ANTIGA and (linhas["colecao"] == r.ABA_ANTIGA).all() else "C1"))
            if l.ean and l.ean != (eans[0] if eans else None):
                de_onde("ean", l.ean, "C12")
            if l.cor != nome_cor[cor]:
                de_onde("cor", l.cor, "C7")
            if l.descontinuado in ("True", ""):
                de_onde("descontinuado", "linha toda" if l.descontinuado == "True" else "só o SKU em vermelho", "C3")
            if l.revisar_preco == "True":
                para_marina.append(dict(sku=v, linha_origem=l.linha_origem, preco=preco, motivo="revisar preço (C10)",
                                        decisao="C10"))
        for l in linhas[trocado.loc[linhas.index]].itertuples():
            para_marina.append(dict(sku=v, linha_origem=l.linha_origem, preco=l.preco_n,
                                    motivo=f"preço e custo trocados na planilha: preço {l.preco}, custo {l.custo}",
                                    decisao="C17"))

    # E10 (como a C4): peça contada de um modelo do catálogo sem a cor ou o tamanho dele vira variante,
    # com o preço mais comum do modelo, à venda enquanto tiver estoque
    feitas = {v["sku"] for v in variantes}
    for l in contada[contada["leitura"] == "modelo do catálogo"].itertuples():
        if l.sku in feitas or estoque.get(l.sku, 0) <= 0:
            continue
        do_modelo = [v for v in variantes if v["produto"] == l.sku[:11]]
        preco = pd.Series([v["preco"] for v in do_modelo]).mode().max()
        _, cat, num, cor, tam = l.sku.split("-")
        variantes.append(dict(sku=l.sku, produto=l.sku[:11], ean=None, cor=nome_cor[cor], tamanho=tam, preco=preco,
                              ativo=True, estoque_01_10=int(estoque[l.sku])))
        historicos.append(dict(sku=l.sku, preco_anterior=None, preco_novo=preco, alterado_em=r.DATA_CONTAGEM_GERAL,
                               decisao="E10"))
        saida.de_onde("variante", l.sku, FONTE_CONTAGEM, l.linha_origem, "E10", "sku", l.codigo)
        feitas.add(l.sku)

    variante = pd.DataFrame(variantes)
    # C12: o EAN não pode repetir entre variantes; o que repetir sai e o trigger gera o interno
    repetido = variante["ean"].notna() & variante["ean"].duplicated(keep=False)
    for l in variante[repetido].itertuples():
        saida.pendente("variante", FONTE_CATALOGO, l.sku, "ean", l.ean, "EAN de fábrica em mais de uma variante (C12)")
    variante.loc[repetido, "ean"] = None

    # produtos: o nome da coleção mais nova, preferindo o que não está todo em maiúsculas (C6), e a
    # categoria pelo código do SKU (C18); as duas descrições com o nome (C5)
    categoria = de_para("categoria").set_index("codigo_sku")
    produtos = []
    for p, linhas in c.groupby("produto", sort=False):
        nomes = [n for n in linhas["nome"].iloc[::-1] if not n.isupper()] or [linhas["nome"].iloc[-1].title()]
        nome = nomes[0]
        produtos.append(dict(codigo=p, nome=nome, categoria=categoria.loc[p[4:7], "categoria"], descricao_tecnica=nome,
                             descricao_cliente=nome, marca="vulto",
                             ativo=bool(variante.loc[variante["produto"] == p, "ativo"].any())))
        saida.de_onde("produto", p, FONTE_CATALOGO, linhas["linha_origem"].iloc[-1], "C6")
        for original in sorted(set(linhas["nome"]) - {nome}):
            saida.de_onde("produto", p, FONTE_CATALOGO, linhas.loc[linhas["nome"] == original, "linha_origem"].iloc[0],
                          "C6", "nome", original)
        for original in sorted(set(linhas["categoria"]) - {""}):
            saida.de_onde("produto", p, FONTE_CATALOGO,
                          linhas.loc[linhas["categoria"] == original, "linha_origem"].iloc[0], "C18", "categoria", original)
    produto = pd.DataFrame(produtos)

    # P8: o produto e a variante genéricos, fora de venda, numa categoria própria e inativa
    produto.loc[len(produto)] = dict(codigo=SKU_SEM_CADASTRO, nome=NOME_SEM_CADASTRO, categoria=CATEGORIA_SEM_CADASTRO,
                                     descricao_tecnica=NOME_SEM_CADASTRO, descricao_cliente=NOME_SEM_CADASTRO,
                                     marca="vulto", ativo=False)
    variante.loc[len(variante)] = dict(sku=SKU_SEM_CADASTRO, produto=SKU_SEM_CADASTRO, ean=None, cor="Única",
                                       tamanho="U", preco=0.0, ativo=False, estoque_01_10=0)
    saida.de_onde("produto", SKU_SEM_CADASTRO, FONTE_DE_PARA, "P8", "P8")

    # C18: só as categorias que a Lorenzi ainda não tem
    novas = categoria.loc[categoria["na_lorenzi"] == "nova", "categoria"].tolist()
    saida.tabela("categoria_produto", pd.DataFrame(
        [dict(nome=n, ativo=True) for n in novas] + [dict(nome=CATEGORIA_SEM_CADASTRO, ativo=False)]))
    saida.tabela("produto", produto)
    saida.tabela("variante", variante)
    saida.tabela("historico_preco", pd.DataFrame(historicos))
    saida.tabela("imagem_produto", pd.DataFrame(
        [dict(produto=p, cor=None, caminho_arquivo=IMAGEM_GENERICA, ordem=1) for p in produto["codigo"]]))
    saida.tabela("lista_marina", pd.DataFrame(para_marina))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--origem", type=Path, default=Path("staging_vulto"))
    p.add_argument("--destino", type=Path, default=Path("tratado_vulto"))
    args = p.parse_args()

    saida = Saida()
    tratar_unidades(args.origem, saida)
    tratar_contas_internas(args.origem, saida)
    tratar_catalogo(args.origem, saida)
    saida.gravar(args.destino)

    for nome, df in saida.tabelas.items():
        print(f"{nome}: {len(df)} linhas")
    print(f"origem_migracao: {len(saida.origem)} linhas | pendencias: {len(saida.pendencias)}")


if __name__ == "__main__":
    main()
