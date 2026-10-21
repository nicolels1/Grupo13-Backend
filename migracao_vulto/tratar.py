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
import re
from datetime import datetime
from functools import cache
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
ILEGIVEL = "ILEGÍVEL "  # prefixo da peça contada sem variante única (E10)
COR_ILEGIVEL = "A identificar"
NOME_SEM_CADASTRO = "Item sem cadastro Vulto"
CATEGORIA_SEM_CADASTRO = "Sem cadastro Vulto"
# C16: a mesma imagem da marca em todo produto, no bucket de fotos de produto; a carga sobe o arquivo
IMAGEM_GENERICA = "vulto/sem-foto.png"


# Vendas classificadas do banco e o estoque na data base (E2, E4, E5, E10, E13), calculados uma vez para o
# catálogo e para o estoque
@cache
def estoque_vulto(origem: Path) -> tuple[pd.DataFrame, dict]:
    vendas, itens = ler(origem, "banco_vendas").replace("", None), ler(origem, "banco_itens_venda").replace("", None)
    lancamentos = r.lancamentos_banco(vendas, itens, ler(origem, "catalogo_produtos").replace("", None))
    lancamentos = lancamentos[lancamentos["linha_origem"].astype(str).str.startswith("vendas#")]
    e = r.estoque_na_data_base(lancamentos, itens, ler(origem, "banco_entradas_estoque").replace("", None),
                               ler(origem, "contagem_set26").replace("", None), ler(origem, "banco_estoque").replace("", None),
                               ler(origem, "catalogo_produtos"))
    return lancamentos, e


# Variante de cada SKU do estoque. No código reaproveitado, o SKU que as duas peças usam fica com a nova e o
# que só a antiga tem fica com ela (C2); o código ilegível da contagem fica com o código original (E10)
def dono_da_peca(skus, variantes: set) -> dict:
    return {s: s.removeprefix(ILEGIVEL) if s.startswith(ILEGIVEL)
            else s + r.SUFIXO_PECA_ANTIGA if s not in variantes and s + r.SUFIXO_PECA_ANTIGA in variantes else s
            for s in set(skus)}


# Estoque de cada variante em 01/10/2026 (posição em 30/09 mais o que está em trânsito, E13). Devolve também
# a contagem lida, de onde vem a variante criada a partir do modelo e a do código ilegível (E10)
def estoque_por_variante(origem: Path, variantes: set) -> tuple[pd.Series, pd.DataFrame]:
    _, e = estoque_vulto(origem)
    q = pd.concat([e["posicao"][["sku", "q"]], e["transito"][["sku", "q"]]]).groupby("sku")["q"].sum()
    return q.groupby(dono_da_peca(q.index, variantes)).sum(), e["contada"]


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
    trocado = (c["custo_n"] > c["preco_n"]) & pd.Series(
        [round(custo, 2) in precos_do_modelo[p] for custo, p in zip(c["custo_n"].fillna(-1), c["produto"])], index=c.index)
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

    # E10: sem variante única, a peça contada entra com o código original numa variante do produto genérico
    # (P8), marcada e fora de venda, para a equipe corrigir na plataforma. O código vai no tamanho, que
    # separa as variantes do mesmo produto
    for l in contada[contada["leitura"] == "ilegível"].itertuples():
        if l.codigo not in feitas:
            variantes.append(dict(sku=l.codigo, produto=SKU_SEM_CADASTRO, ean=None, cor=COR_ILEGIVEL, tamanho=l.codigo,
                                  preco=0.0, ativo=False, estoque_01_10=int(estoque[l.codigo])))
            feitas.add(l.codigo)
        saida.de_onde("variante", l.codigo, FONTE_CONTAGEM, l.linha_origem, "E10")

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


# ---------- clientes ----------

FONTE_CLIENTES = "clientes_fidelidade_FINAL_v3 (1).xlsx"
FONTE_BATEL = "vendas_batel_MANUAL_marco.xlsx"
ORIGEM_CLIENTE = "migracao_vulto"


# Identidade do comprador de cada venda do banco (J1a, J1b, P2), já com a ponte e-mail → CPF do
# atendimento. Devolve as regras (para a Batel e as outras etapas) e a identidade por id da venda
def compradores(origem: Path, clientes: pd.DataFrame) -> tuple[r.Compradores, pd.Series]:
    vendas = ler(origem, "banco_vendas").replace("", None)
    atendimentos = ler(origem, "atendimentos").replace("", None)
    c = r.Compradores(clientes)

    def identidades():
        return pd.Series([c.identidade(t, v) for t, v in zip(vendas["cliente_tipo"], vendas["cliente_valor"])],
                         index=vendas["id"], dtype=object)

    sem_ponte = identidades()
    c.ponte = r.ponte_pelo_atendimento(atendimentos, r.venda_do_atendimento(atendimentos, vendas)["id_venda"],
                                       sem_ponte, c)
    return c, identidades()


# Telefone do cadastro só com os dígitos, sem o +55. O anotado sem DDD (vazio no staging, valor nas
# pendências) ganha o DDD achado em outra fonte ou fica com os 9 dígitos, incompleto (K9a)
def telefones(origem: Path, clientes: pd.DataFrame, pendencias: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    ddd = r.ddd_dos_sem_ddd(clientes, ler(origem, "atendimentos").replace("", None),
                            ler(origem, "banco_vendas").replace("", None), ler(origem, "banco_compras").replace("", None))
    sem_ddd = pendencias[(pendencias["coluna"] == "Tel") & pendencias["motivo"].str.contains("DDD")] \
        .set_index("linha")["valor"]
    original = clientes["linha_origem"].map(sem_ddd)
    nove = original.map(r.so_digitos).str[-9:]
    completado = nove.map(lambda n: ddd.get(n, "") + n if isinstance(n, str) else None)
    return clientes["Tel"].map(r.so_digitos).str[2:].fillna(completado), original


# P2: todo cliente da Vulto vira cliente sem conta, um por CPF (sem CPF, um por e-mail). Entram o cadastro
# do Vulto+ (os cadastros do mesmo CPF juntos, K1), os só da aba antiga (K5d), os CPFs só nos pontos (K8)
# e os compradores com CPF ou e-mail fora do Vulto+ (J1a). Devolve a quantidade por origem
def tratar_clientes(origem: Path, saida: Saida) -> pd.Series:
    clientes = ler(origem, "clientes_clientes").replace("", None)
    antigos = ler(origem, "clientes_antigos").replace("", None)
    pendencias = ler(origem, "pendencias").replace("", None)
    pontos = ler(origem, "banco_pontos_mov").replace("", None)
    compras = ler(origem, "banco_compras").replace("", None)
    resgates = ler(origem, "clientes_resgates").replace("", None)
    vendas = ler(origem, "banco_vendas").replace("", None)
    batel = ler(origem, "batel_vendas_manuais").replace("", None)

    conhecidos = r.cpfs_conhecidos(clientes["cpf"], pontos["cpf"], resgates["cliente_cpf"], antigos["CPF/CNPJ"],
                                   compras["cliente_ref"], vendas["cliente_ref"])
    # o dono dos pontos sai do cadastro como o numeros.py o lê, para o passivo da carga ser o mesmo
    ident = r.Identidades(clientes.copy(), pendencias, conhecidos)
    clientes["cpf"], cpf_digitado = r.cpf_do_cadastro(clientes, pendencias, conhecidos)
    clientes["telefone"], tel_original = telefones(origem, clientes, pendencias)
    clientes["resposta"] = [r.consentimento(a, o) for a, o in zip(clientes["aceita_msg"], clientes["Obs"])]
    clientes["chave"] = clientes["cpf"].fillna(clientes["E-mail"].str.lower())

    linhas, de_onde = {}, {}  # chave → cliente; chave → origem principal (para a contagem)
    cad = clientes.rename(columns={"Nome": "nome", "E-mail": "email", "Data Cadastro": "data_cadastro"})
    for l in cad[cad["chave"].isna()].itertuples():
        saida.pendente("cliente_sem_conta", FONTE_CLIENTES, l.linha_origem, "cpf", cpf_digitado[l.Index],
                       "cadastro sem CPF válido e sem e-mail (K2, P2)")
    for chave, grupo in cad.dropna(subset=["chave"]).sort_values(["data_cadastro", "linha_origem"]).groupby(
            "chave", sort=False):
        # K1: contato do cadastro mais recente (o último preenchido); data de cadastro do mais antigo
        recente = lambda col: next((v for v in grupo[col].iloc[::-1] if isinstance(v, str)), None)
        cliente = dict(cpf=grupo["cpf"].iloc[0], email=recente("email"), nome=recente("nome"),
                       telefone=recente("telefone"), aceita_mensagem=r.consentimento_juntado(grupo["resposta"]),
                       origem=ORIGEM_CLIENTE, criado_em=grupo["data_cadastro"].iloc[0])
        linhas[chave], de_onde[chave] = cliente, "cadastro do Vulto+"
        decisao = "K1" if len(grupo) > 1 else "P2"
        for i, l in enumerate(grupo.itertuples()):
            onde = lambda decisao, campo=None, valor=None: saida.de_onde(
                "cliente_sem_conta", chave, FONTE_CLIENTES, l.linha_origem, decisao, campo, valor)
            onde(decisao)
            if isinstance(cpf_digitado[l.Index], str):
                onde("K2", "cpf", cpf_digitado[l.Index])
            if isinstance(tel_original[l.Index], str):
                onde("K9a", "telefone", tel_original[l.Index])
            if l.aceita_msg not in ("sim", "nao", None) or isinstance(l.Obs, str) and l.resposta == "nao_aceita":
                onde("K9b", "aceita_mensagem", " | ".join(v for v in (l.aceita_msg, l.Obs) if isinstance(v, str)))
            for campo in ("nome", "email", "telefone"):
                valor = getattr(l, campo)
                if isinstance(valor, str) and valor != cliente[campo]:
                    onde("K1", campo, valor)
            if i > 0:
                onde("K1", "criado_em", l.data_cadastro)
            if isinstance(l.loja_cadastro, str):
                onde("K1" if len(grupo) > 1 else "P2", "loja_cadastro", l.loja_cadastro)

    # K5c, K5d: quem está nas duas abas fica com o cadastro da aba nova; os só da aba antiga entram sem contato
    for l in antigos.rename(columns={"NOME COMPLETO": "nome", "CPF/CNPJ": "cpf"}).itertuples():
        cpf = l.cpf
        if cpf not in linhas:
            linhas[cpf], de_onde[cpf] = dict(cpf=cpf, email=None, nome=l.nome, telefone=None, aceita_mensagem=None,
                                             origem=ORIGEM_CLIENTE, criado_em=None), "só na aba antiga (K5d)"
            decisao = "K5d"
        else:
            decisao = "K5c"
        saida.de_onde("cliente_sem_conta", cpf, FONTE_CLIENTES, l.linha_origem, decisao)

    # K8: o dono de cada movimento de pontos (J1c, J1d, K2) que não está na planilha entra sem nome
    p = r.dono_dos_pontos(pontos, compras, pendencias, ident).dropna(subset=["dono"])
    for dono, grupo in p.sort_values("criado_em").groupby("dono"):
        if dono not in linhas:
            linhas[dono], de_onde[dono] = dict(cpf=dono, email=None, nome=None, telefone=None, aceita_mensagem=None,
                                               origem=ORIGEM_CLIENTE, criado_em=grupo["criado_em"].iloc[0]), \
                "só nos pontos (K8)"
            saida.de_onde("cliente_sem_conta", dono, FONTE_BANCO, grupo["linha_origem"].iloc[0], "K8")

    # J1a, P2: comprador com CPF ou só com e-mail fora do Vulto+; a venda de teste não cria cliente (V8)
    comp, identidade = compradores(origem, clientes)
    teste = pd.Series([r.eh_teste(*t) for t in zip(vendas["obs"], vendas["cliente_ref"], vendas["cliente_valor"],
                                                    vendas["pedido_externo"].fillna(""))], index=vendas.index)
    v = vendas[~teste].assign(identidade=vendas["id"].map(identidade)).dropna(subset=["identidade"])
    batel_cpf = batel.dropna(subset=["CPF"]).assign(identidade=lambda b: b["CPF"].map(lambda c: comp.identidade("cpf", c)),
                                                     criado_em=lambda b: b["DATA"].map(r.data_batel))
    compradores_fora = pd.concat([
        v[["identidade", "criado_em", "linha_origem"]].assign(fonte=FONTE_BANCO),
        batel_cpf[["identidade", "criado_em", "linha_origem"]].assign(fonte=FONTE_BATEL)])
    for chave, grupo in compradores_fora.dropna(subset=["identidade"]).sort_values("criado_em").groupby("identidade"):
        if chave in linhas:
            continue
        por_email = "@" in chave
        linhas[chave] = dict(cpf=None if por_email else chave, email=chave if por_email else None, nome=None,
                             telefone=None, aceita_mensagem=None, origem=ORIGEM_CLIENTE,
                             criado_em=grupo["criado_em"].iloc[0])
        de_onde[chave] = "comprador só com e-mail (J1a)" if por_email else "comprador com CPF fora do Vulto+"
        saida.de_onde("cliente_sem_conta", chave, grupo["fonte"].iloc[0], grupo["linha_origem"].iloc[0],
                      "J1a" if por_email else "P2")

    saida.tabela("cliente_sem_conta", pd.DataFrame(linhas.values()))
    return pd.Series(de_onde).value_counts()


# ---------- vendas ----------

STATUS_LORENZI = dict(zip(de_para("status_venda")["situacao"], de_para("status_venda")["status_lorenzi"]))
METODO = dict(zip(de_para("metodo_pagamento")["valor_vulto"], de_para("metodo_pagamento")["metodo"]))
CONTA_MIGRACAO = de_para("conta_interna").set_index("conta").loc["migracao", "email"]
EMAIL_DA_CONTA = de_para("conta_interna").set_index("conta")["email"]
JUSTIFICATIVA = {"V14": "Cancelado no sistema da Vulto, motivo não registrado",
                 "V3": "Aguardando pagamento no sistema da Vulto, sem pagamento registrado"}
NAO_MIGRA = ("V8 teste", "V5 cópia do pedido", "V9 relançamento da Batel")


# Identidade do comprador de cada venda do banco: a dos compradores (J1a, J1b, P2) e, no cliente_ref que
# o staging não aceitou (nas pendências), o dono do celular sem DDD (J1d) ou do CPF digitado errado (K2)
def identidade_das_vendas(origem: Path) -> tuple[pd.Series, pd.Series, r.Compradores]:
    clientes = ler(origem, "clientes_clientes").replace("", None)
    pendencias = ler(origem, "pendencias").replace("", None)
    conhecidos = r.cpfs_conhecidos(clientes["cpf"], ler(origem, "banco_pontos_mov").replace("", None)["cpf"],
                                   ler(origem, "clientes_resgates").replace("", None)["cliente_cpf"],
                                   ler(origem, "clientes_antigos").replace("", None)["CPF/CNPJ"],
                                   ler(origem, "banco_compras").replace("", None)["cliente_ref"],
                                   ler(origem, "banco_vendas").replace("", None)["cliente_ref"])
    ident = r.Identidades(clientes.copy(), pendencias, conhecidos)
    clientes["cpf"], _ = r.cpf_do_cadastro(clientes, pendencias, conhecidos)
    comp, identidade = compradores(origem, clientes)
    digitado = pendencias[(pendencias["coluna"] == "cliente_ref") & pendencias["linha"].str.startswith("vendas#")]
    regra = pd.Series(dtype=object)
    for linha, valor in zip(digitado["linha"], digitado["valor"]):
        d = r.so_digitos(valor)
        dono, regra[linha] = ident.onze_digitos(d, "." in valor) if len(d) == 11 else ident.nove_digitos(d)
        if dono and dono != r.CPF_TESTE:
            identidade[linha.removeprefix("vendas#")] = dono
    return identidade, regra, comp


def em_utc(quando: str) -> str:
    return datetime.fromisoformat(quando).astimezone(r.ZoneInfo("UTC")).strftime("%Y-%m-%d %H:%M:%S+00:00")


# Rateia o valor da venda da Batel entre as peças pelo preço de referência de cada uma (a planilha só tem
# o valor da venda). Os centavos que sobram do arredondamento vão para uma peça de quantidade 1
def ratear(valor: float, pecas: list[dict]) -> list[float]:
    soma = sum(p["referencia"] * p["quantidade"] for p in pecas)
    unitarios = [round(valor * p["referencia"] / soma, 2) for p in pecas]
    resto = round(valor - sum(u * p["quantidade"] for u, p in zip(unitarios, pecas)), 2)
    uma = next((i for i, p in enumerate(pecas) if p["quantidade"] == 1), None)
    if resto and uma is not None:
        unitarios[uma] = round(unitarios[uma] + resto, 2)
    return unitarios


# V2–V19, P3, P7, V6, V9: um pedido por venda que migra (as canceladas e as aguardando como canceladas),
# com os itens e o pagamento; o estorno do V4 e o da devolução (V6) apontam para o pagamento original.
# A troca e a devolução não viram pedido; a devolução sem pedido original fica nas pendências (V6). Devolve
# também o pedido original de cada devolução e troca, que a movimentação de estoque delas cita
def tratar_vendas(origem: Path, saida: Saida) -> dict:
    vendas = ler(origem, "banco_vendas").replace("", None)
    itens = ler(origem, "banco_itens_venda").replace("", None)
    catalogo = ler(origem, "catalogo_produtos").replace("", None)
    batel = ler(origem, "batel_vendas_manuais").replace("", None)
    variantes = saida.tabelas["variante"].merge(saida.tabelas["produto"], left_on="produto", right_on="codigo")
    skus = set(variantes["sku"])
    reaproveitados = r.codigos_reaproveitados(catalogo)
    clientes = set(saida.tabelas["cliente_sem_conta"]["cpf"].dropna()) | \
        set(saida.tabelas["cliente_sem_conta"]["email"].dropna())

    l = r.lancamentos_banco(vendas, itens, catalogo)
    identidade, regra_dono, comp = identidade_das_vendas(origem)
    l["identidade"] = l["id"].map(identidade)
    l["dia"] = l["criado_em"].map(r.dia_brasilia)
    pedidos, linhas_itens, pagamentos = [], [], []

    def cliente(chave, tabela, fonte, linha):
        if isinstance(chave, str) and chave not in clientes:
            saida.pendente(tabela, fonte, linha, "cliente", chave, "comprador fora da tabela de clientes")
            return None
        return chave

    def pagar(codigo, valor, quando, metodo, fonte, linha):
        chave = f"{codigo}#pagamento"
        pagamentos.append(dict(chave=chave, pedido=codigo, tipo="pagamento", pagamento_original=None, origem="migracao",
                               metodo=metodo, valor=valor, status="aprovado", criado_em=quando))
        saida.de_onde("pagamento", chave, fonte, linha, "P7")
        return chave

    def estornar(codigo, original, valor, quando, metodo, fonte, linha, decisao, n=1):
        chave = f"{codigo}#estorno{n}"
        pagamentos.append(dict(chave=chave, pedido=codigo, tipo="estorno", pagamento_original=original,
                               origem="migracao", metodo=metodo, valor=valor, status="aprovado", criado_em=quando))
        saida.de_onde("pagamento", chave, fonte, linha, decisao)

    migra = l[(l["tipo"] == "venda") & ~l["motivo"].isin(NAO_MIGRA)]
    itens_por_venda = itens.groupby("venda_id")
    codigo_do_id = {}
    for v in migra.itertuples():
        codigo = r.codigo_venda(v.pedido_externo, v.id)
        codigo_do_id[v.id] = codigo
        status = STATUS_LORENZI[v.situacao]
        cancelado = status == "cancelado"
        loja = v.canal == "loja"
        conta = r.vendedor(v.loja_id, v.vendedor, v.dia) if loja else None
        if loja and isinstance(v.vendedor, str) and conta is None:
            saida.pendente("pedido", FONTE_BANCO, v.linha_origem, "vendedor", v.vendedor, "grafia sem conta (P9)")
        chave_cliente = cliente(v.identidade, "pedido", FONTE_BANCO, v.linha_origem)
        if not loja and chave_cliente is None:
            saida.pendente("pedido", FONTE_BANCO, v.linha_origem, "cliente", v.cliente_ref,
                           "pedido online sem cliente (online_exige_cliente)")
        escala = 0.01 if v.em_centavos else 1.0
        pedidos.append(dict(
            codigo_venda=codigo, cliente=chave_cliente, unidade=v.unidade,
            registrado_por=EMAIL_DA_CONTA.get(conta), canal="loja_fisica" if loja else "online", canal_venda=v.canal,
            modalidade=None if loja else "entrega", status=status, motivo_cancelamento="equipe" if cancelado else None,
            cancelado_por=CONTA_MIGRACAO if cancelado else None,
            justificativa_cancelamento=JUSTIFICATIVA["V3" if v.situacao == "aguardando" else "V14"] if cancelado else None,
            devolucao="nenhuma", valor_frete=v.frete_venda, valor_desconto=round(v.desconto * escala, 2),
            valor_total=v.corrigido, pago_em=None if cancelado else v.criado_em,
            entregue_em=v.criado_em if loja and not cancelado else None, criado_em=v.criado_em))

        onde = lambda decisao, campo=None, valor=None: saida.de_onde("pedido", codigo, FONTE_BANCO, v.linha_origem,
                                                                     decisao, campo, valor)
        onde("V13" if not cancelado else ("V3" if v.situacao == "aguardando" else "V14"))
        if not isinstance(v.pedido_externo, str):
            onde("V15", "codigo_venda", v.id)
        if v.status != "pago":
            onde({"pago": "V2" if pd.isna(v.status) else "V13", "aguardando": "V3", "cancelado": "V14",
                  "estornado": "V4", "chargeback": "V4"}[v.situacao], "status", v.status)
        if v.em_centavos:
            onde("V1", "valor_total", v.total)
        if v.canal == "app" and v.loja_id != "6":
            onde("P3", "loja_id", v.loja_id)
        if v.linha_origem in regra_dono.index:
            onde("J1d" if regra_dono[v.linha_origem].startswith("J1d") else "K2", "cliente_ref", v.cliente_ref)
        if v.cliente_tipo == "cnpj":
            onde("P2", "cliente_ref", v.cliente_ref)

        for i in itens_por_venda.get_group(v.id).itertuples() if v.id in itens_por_venda.groups else []:
            variante = r.variante_vendida(r.sku_oficial(i.sku), i.descricao, reaproveitados)
            if variante not in skus:
                if variante is not None:
                    saida.pendente("item_pedido", FONTE_BANCO, i.linha_origem, "sku", i.sku, "variante fora do catálogo")
                variante = SKU_SEM_CADASTRO
            linhas_itens.append(dict(item=i.linha_origem, pedido=codigo, variante=variante, quantidade=int(i.qtd),
                                     preco_unitario=round(float(i.preco_unit) * escala, 2)))
            if variante == SKU_SEM_CADASTRO:
                saida.de_onde("item_pedido", i.linha_origem, FONTE_BANCO, i.linha_origem, "P8", "variante",
                              f"{i.sku} | {i.descricao}")
            if v.em_centavos:
                saida.de_onde("item_pedido", i.linha_origem, FONTE_BANCO, i.linha_origem, "V1", "preco_unitario",
                              i.preco_unit)

        if not cancelado:
            original = pagar(codigo, v.corrigido, v.criado_em, METODO["(vazio)"], FONTE_BANCO, v.linha_origem)
            if v.situacao in ("estornado", "chargeback"):
                estornar(codigo, original, v.corrigido, v.criado_em, METODO["(vazio)"], FONTE_BANCO, v.linha_origem, "V4")

    # V5: a cópia do pedido fica na origem do pedido que migrou
    for v in l[l["motivo"] == "V5 cópia do pedido"].itertuples():
        saida.de_onde("pedido", v.pedido_externo, FONTE_BANCO, v.linha_origem, "V5")

    p = pd.DataFrame(pedidos).set_index("codigo_venda")
    vendidos_planilha = []  # peças da planilha da Batel, onde a devolução também procura a compra (V6)

    # V9: a planilha da Batel em 02–22/03; a peça achada pelo texto, o resto na variante genérica (P8)
    ref_variante = variantes.assign(nome_n=variantes["nome"].map(r.nome_normalizado),
                                    cor_c=variantes["sku"].str[12:15])
    ref_modelo = ref_variante.groupby("nome_n")["preco"].median()
    for b in batel[batel["QTD"].notna()].itertuples():
        codigo = r.codigo_batel(b.linha_origem)
        dia = r.data_batel(b.DATA)
        # sem hora: vale o dia
        quando = datetime.fromisoformat(f"{dia}T{b.HORA or '00:00'}").replace(tzinfo=r.FUSO_BRASILIA).isoformat()
        obs = (b.OBS or "").lower()
        cancelado = "cancel" in obs
        valor = float(b.VALOR)
        pecas = []
        for qtd, nome, cor, tam in r.pecas_batel(b.PEÇAS):
            achou = ref_variante[(ref_variante["nome_n"] == nome) & (ref_variante["cor_c"] == cor)
                                 & (ref_variante["tamanho"] == tam)]
            variante = achou["sku"].iloc[0] if len(achou) == 1 else SKU_SEM_CADASTRO
            referencia = achou["preco"].iloc[0] if len(achou) == 1 else ref_modelo.get(nome, 1.0)
            pecas.append(dict(quantidade=qtd, variante=variante, referencia=float(referencia), texto=nome))
        if sum(pc["quantidade"] for pc in pecas) != int(b.QTD):
            saida.pendente("item_pedido", FONTE_BATEL, b.linha_origem, "QTD", b.QTD, "quantidade diferente das peças")
        for n, (pc, unit) in enumerate(zip(pecas, ratear(valor, pecas)), 1):
            item = f"{b.linha_origem}#{n}"
            linhas_itens.append(dict(item=item, pedido=codigo, variante=pc["variante"], quantidade=pc["quantidade"],
                                     preco_unitario=unit))
            if not cancelado:
                vendidos_planilha.append(dict(id=codigo, loja_id=r.LOJA_BATEL, dia=dia, peca=pc["variante"], preco=unit,
                                              criado_em=em_utc(quando), identidade=comp.identidade("cpf", b.CPF)))
            if pc["variante"] == SKU_SEM_CADASTRO:
                saida.de_onde("item_pedido", item, FONTE_BATEL, b.linha_origem, "P8", "variante", pc["texto"])
        chave_cliente = cliente(comp.identidade("cpf", b.CPF), "pedido", FONTE_BATEL, b.linha_origem)
        conta = r.vendedor(r.LOJA_BATEL, b.VENDEDORA, dia)
        p.loc[codigo] = pd.Series(dict(
            cliente=chave_cliente, unidade=r.LOJAS_FISICAS[r.LOJA_BATEL], registrado_por=EMAIL_DA_CONTA.get(conta),
            canal="loja_fisica", canal_venda="loja", modalidade=None, status="cancelado" if cancelado else "entregue",
            motivo_cancelamento="equipe" if cancelado else None, cancelado_por=CONTA_MIGRACAO if cancelado else None,
            justificativa_cancelamento=JUSTIFICATIVA["V14"] if cancelado else None, devolucao="nenhuma",
            valor_frete=0.0, valor_desconto=0.0, valor_total=valor, pago_em=None if cancelado else quando,
            entregue_em=None if cancelado else quando, criado_em=quando))
        saida.de_onde("pedido", codigo, FONTE_BATEL, b.linha_origem, "V9")
        if b.DATA.endswith("00:00:00") or len(b.DATA) == 5:  # o Excel trocou dia e mês ou não tem o ano
            saida.de_onde("pedido", codigo, FONTE_BATEL, b.linha_origem, "V9", "criado_em", b.DATA)
        if not cancelado:
            metodo = METODO[b.PGTO.strip().lower()]
            original = pagar(codigo, valor, quando, metodo, FONTE_BATEL, b.linha_origem)
            if "estorn" in obs:
                estornar(codigo, original, valor, quando, metodo, FONTE_BATEL, b.linha_origem, "V4")

    # V6: a devolução vira estorno no pagamento do pedido original. Sem original pela regra, vale a exceção:
    # o relançamento citado na obs ou a compra da mesma peça na mesma loja (r.compras_possiveis); o
    # relançamento da gerente leva à venda da planilha que ele repete (V9)
    relancamentos = l[l["motivo"] == "V9 relançamento da Batel"]
    planilha = batel[batel["QTD"].notna()].assign(identidade=lambda b: b["CPF"].map(lambda c: comp.identidade("cpf", c)),
                                                 valor=lambda b: pd.to_numeric(b["VALOR"]))
    for relancamento, linha in r.par_na_planilha(relancamentos, planilha).items():
        codigo_do_id[relancamento] = r.codigo_batel(linha)
    codigo_do_id.update({v["id"]: v["id"] for v in vendidos_planilha})
    pecas = itens.assign(peca=itens["sku"].map(r.sku_oficial).fillna(itens["sku"]),
                         preco=pd.to_numeric(itens["preco_unit"]).abs())
    vendidos = pecas.drop(columns="id").merge(l[l["id"].isin(codigo_do_id) & (l["tipo"] == "venda") & ~l["situacao"].isin(
        ["cancelado", "aguardando"])][["id", "loja_id", "criado_em", "dia", "identidade"]], left_on="venda_id", right_on="id")
    vendidos = pd.concat([vendidos, pd.DataFrame(vendidos_planilha)], ignore_index=True)
    estornado = pd.DataFrame(pagamentos).query("tipo == 'estorno'").groupby("pedido")["valor"].sum()
    estornado = estornado.reindex(p.index).fillna(0.0)
    n_estornos = pd.Series(1, index=estornado[estornado > 0].index).reindex(p.index).fillna(0).astype(int)
    originais = r.pedido_original(l, itens)
    devolucoes = l[(l["tipo"] == "devolucao") & l["motivo"].isna()].sort_values("criado_em")
    sem_original = 0.0
    trocas = l.loc[l["tipo"] == "troca", "id"]
    original = {i: codigo_do_id[originais[i][0]] for i in trocas if i in originais and originais[i][0] in codigo_do_id}
    for d in devolucoes.itertuples():
        valor = round(-d.corrigido, 2)
        possiveis = [originais[d.id]] if d.id in originais else []
        ref = re.search(r.REF_PEDIDO, r.sem_acento(str(d.obs or "")).lower())
        devolvidas = pecas[pecas["venda_id"] == d.id]
        if ref and ref[1] in relancamentos["id"].values:
            # relançamento sem par na planilha: a venda da planilha de mesmo valor com a peça devolvida
            valor_rel = relancamentos.loc[relancamentos["id"] == ref[1], "corrigido"].iloc[0]
            mesma = vendidos[vendidos["id"].str.startswith("VLT-BATEL-") & vendidos["peca"].isin(devolvidas["peca"])]
            mesma = mesma[mesma["id"].map(lambda c: abs(p.loc[c, "valor_total"] - valor_rel) < 0.005)]
            possiveis += [(ref[1], "relançamento citado na obs")] +                 [(c, "relançamento citado na obs") for c in mesma["id"].drop_duplicates()]
        for peca in devolvidas.itertuples():
            devolvida = pd.Series(dict(loja_id=d.loja_id, criado_em=d.criado_em, dia=d.dia, peca=peca.peca,
                                       preco=peca.preco))
            possiveis += r.compras_possiveis(devolvida, vendidos)
        codigo, regra = next(((codigo_do_id[i], regra) for i, regra in possiveis if i in codigo_do_id
                              and estornado[codigo_do_id[i]] + valor <= p.loc[codigo_do_id[i], "valor_total"] + 0.005),
                             (None, None))
        if codigo is None:
            sem_original += valor
            saida.pendente("pagamento", FONTE_BANCO, d.linha_origem, "valor", valor,
                           "devolução sem nenhuma compra possível (V6)")
            continue
        estornado[codigo] += valor
        n_estornos[codigo] += 1
        original[d.id] = codigo
        estornar(codigo, f"{codigo}#pagamento", valor, d.criado_em, METODO["(vazio)"], FONTE_BANCO, d.linha_origem,
                 "V6", n_estornos[codigo])
        saida.de_onde("pagamento", f"{codigo}#estorno{n_estornos[codigo]}", FONTE_BANCO, d.linha_origem, "V6",
                      "pedido_original", regra)
        itens_valor = p.loc[codigo, "valor_total"] - p.loc[codigo, "valor_frete"]
        p.loc[codigo, "devolucao"] = "total" if estornado[codigo] >= itens_valor - 0.005 else "parcial"

    saida.tabela("pedido", p.reset_index())
    saida.tabela("item_pedido", pd.DataFrame(linhas_itens))
    saida.tabela("pagamento", pd.DataFrame(pagamentos))
    return dict(sem_original=round(sem_original, 2), original=original)


# Faturamento que a plataforma vai calcular com os pedidos migrados: pagamentos menos estornos, sem o
# frete dos pedidos que contam (V7), por mês de Brasília. Tem de fechar com o numeros.md
def faturamento_tratado(saida: Saida) -> pd.Series:
    pg = saida.tabelas["pagamento"].merge(saida.tabelas["pedido"][["codigo_venda", "valor_frete"]],
                                         left_on="pedido", right_on="codigo_venda")
    pg["mes"] = pg["criado_em"].map(r.mes_brasilia)
    pg["valor_fat"] = pg["valor"].where(pg["tipo"] == "pagamento", -pg["valor"])
    estornados_v4 = set(pg.loc[(pg["tipo"] == "estorno") & pg["chave"].str.endswith("#estorno1")
                               & (pg["valor"] == pg["pedido"].map(pg[pg["tipo"] == "pagamento"]
                                                                  .set_index("pedido")["valor"])), "pedido"])
    frete = pg[(pg["tipo"] == "pagamento") & ~pg["pedido"].isin(estornados_v4)]
    fat = pg.groupby("mes")["valor_fat"].sum().sub(frete.groupby("mes")["valor_frete"].sum(), fill_value=0)
    return fat[(fat.index >= r.INICIO) & (fat.index <= r.FIM)].round(2)


# ---------- estoque ----------

FIM_DO_HISTORICO = "2026-09-30"  # a posição é o fim deste dia (E2)
MOTIVO_AJUSTE = "Saída não registrada na Vulto"  # E2
MOTIVO_SALDO_INICIAL = "Saldo em 01/01/2025, reconstruído a partir da contagem de set/2026"  # E2
MOTIVO_ONLINE_PINHEIROS = "Estoque online da Pinheiros, separado da contagem (E7)"
MOTIVO_SEM_ORIGINAL = {"devolucao": "Devolução sem o pedido original na Vulto (V6)",
                       "saida_troca": "Troca sem o pedido original na Vulto (V6)"}
TIPO_DA_VENDA = {"venda": "venda", "devolucao": "devolucao", "troca": "saida_troca"}
# ordem dentro do dia, para o saldo nunca ficar negativo na carga: o saldo inicial, as entradas, a venda
# cancelada (sai e volta), as saídas, o ajuste de saída do dia e, no fim de 30/09, a separação da E7
GRUPO = {"saldo_inicial": 0, "entrada": 1, "cancelada": 2, "saida": 3, "ajuste": 4, "realocacao": 5}


def no_dia(dia: str, hora: str = "00:00:00") -> str:
    return em_utc(datetime.fromisoformat(f"{dia}T{hora}").replace(tzinfo=r.FUSO_BRASILIA).isoformat())


# E1–E14, V10, V17: o histórico de movimentações de 01/01/2025 a 30/09/2026, que o trigger soma no saldo de
# cada peça. Começa no saldo inicial reconstruído (E2) e segue com as vendas, devoluções e trocas (o ship from
# store sai da Pinheiros, V10; a cancelada sai e volta, V17), os recebimentos de fornecedor, as transferências
# (E4; a em trânsito fica enviada, E13) e os ajustes de saída sem registro (E2). No fim, o online da Pinheiros
# (E7) e o mínimo do CD (E14). A carga insere as movimentações na ordem da coluna `ordem`
def tratar_estoque(origem: Path, saida: Saida, original: dict) -> dict:
    l, e = estoque_vulto(origem)
    itens = ler(origem, "banco_itens_venda").replace("", None)
    variantes = set(saida.tabelas["variante"]["sku"])
    pedidos = set(saida.tabelas["pedido"]["codigo_venda"])
    venda = l.set_index("id")
    codigo = lambda i: (lambda c: c if c in pedidos else None)(r.codigo_venda(venda.loc[i, "pedido_externo"], i))

    movimentos = e["movimentos"]
    # V9: a venda da planilha da Batel baixa a peça achada no catálogo (a variante genérica não tem estoque,
    # P8) e entra no histórico (E2); o relançamento dela no banco não baixa. A cancelada sai e volta (V17)
    do_pedido = saida.tabelas["pedido"].set_index("codigo_venda")
    it = saida.tabelas["item_pedido"]
    it = it[it["pedido"].str.startswith("VLT-BATEL-") & (it["variante"] != SKU_SEM_CADASTRO)]
    planilha = pd.DataFrame({"unidade": r.LOJAS_FISICAS[r.LOJA_BATEL],
                             "sku": it["variante"].str.removesuffix(r.SUFIXO_PECA_ANTIGA), "q": -it["quantidade"],
                             "origem": "venda da planilha da Batel", "pedido": it["pedido"], "linha_origem": it["item"],
                             "criado_em": it["pedido"].map(do_pedido["criado_em"]).map(em_utc),
                             "cancelada": it["pedido"].map(do_pedido["status"]) == "cancelado"})
    planilha["dia"] = planilha["criado_em"].map(r.dia_brasilia)
    historico = pd.concat([movimentos, planilha.loc[~planilha["cancelada"], ["unidade", "sku", "dia", "q", "origem"]]],
                          ignore_index=True)
    ajustes = r.reconstruir_historico(e["contada"], historico, e["datas"])[1]
    canceladas = r.itens_cancelados(l, itens)
    canceladas = canceladas[(canceladas["tipo"] == "venda") & canceladas["sku"].notna()]
    online = r.separar_canal(e["posicao"][e["posicao"]["q"] > 0], e["vendidos"])
    online = online[(online["canal"] == "online") & (online["unidade"] == r.LOJAS_FISICAS["1"])]
    canal = lambda unidade: "online" if unidade == r.CD else "loja_fisica"

    linhas = []

    def mover(unidade, sku, tipo, q, dia, criado_em, grupo, decisao, fonte=FONTE_BANCO, linha_origem=None, motivo=None,
              pedido=None, transferencia=None, usuario=None, canal_mov=None):
        linhas.append(dict(sku=sku, unidade=unidade, canal=canal_mov or canal(unidade), tipo=tipo, quantidade=int(q),
                           motivo=motivo, pedido=pedido, transferencia=transferencia, usuario=usuario,
                           criado_em=criado_em, dia=dia, grupo=GRUPO[grupo], decisao=decisao, fonte=fonte,
                           linha_origem=linha_origem))

    for (unidade, sku), q in r.saldo_inicial_estoque(e["posicao"], historico, ajustes).items():
        mover(unidade, sku, "saldo_inicial", q, r.INICIO_HISTORICO, no_dia(r.INICIO_HISTORICO), "saldo_inicial", "E2",
              FONTE_CONTAGEM, motivo=MOTIVO_SALDO_INICIAL)
    for m in movimentos[movimentos["venda_id"].notna()].itertuples():
        tipo = TIPO_DA_VENDA[m.tipo]
        # a devolução e a troca citam o pedido original (V6)
        pedido = codigo(m.venda_id) if tipo == "venda" else original.get(m.venda_id)
        grupo = "entrada" if m.q > 0 else "saida"
        if tipo != "venda" and pedido is None:
            mover(m.unidade, m.sku, "ajuste", m.q, m.dia, em_utc(m.criado_em), grupo, "V6", linha_origem=m.linha_origem,
                  motivo=MOTIVO_SEM_ORIGINAL[tipo])
            continue
        if pedido is None:
            saida.pendente("movimentacao_estoque", FONTE_BANCO, m.linha_origem, "pedido", m.venda_id,
                           "venda que baixa o estoque sem pedido migrado")
        sfs = m.unidade == r.LOJAS_FISICAS["1"] and m.origem != "venda loja"
        mover(m.unidade, m.sku, tipo, m.q, m.dia, em_utc(m.criado_em), grupo,
              "V10" if sfs else ("E2" if tipo == "venda" else "V6"), linha_origem=m.linha_origem, pedido=pedido,
              usuario=None if tipo == "venda" else CONTA_MIGRACAO)
    for m in canceladas.itertuples():
        for tipo, q in (("venda", m.q), ("retorno_cancelamento", -m.q)):
            mover(m.unidade, m.sku, tipo, q, m.dia, em_utc(m.criado_em), "cancelada", "V17", linha_origem=m.linha_origem,
                  pedido=codigo(m.venda_id))
    for m in planilha.itertuples():
        if m.cancelada:
            for tipo, q in (("venda", m.q), ("retorno_cancelamento", -m.q)):
                mover(m.unidade, m.sku, tipo, q, m.dia, m.criado_em, "cancelada", "V17", FONTE_BATEL, m.linha_origem,
                      pedido=m.pedido)
        else:
            mover(m.unidade, m.sku, "venda", m.q, m.dia, m.criado_em, "saida", "V9", FONTE_BATEL, m.linha_origem,
                  pedido=m.pedido)
    for m in movimentos[movimentos["venda_id"].isna()].itertuples():
        if pd.isna(m.transferencia):
            mover(m.unidade, m.sku, "recebimento", m.q, m.dia, no_dia(m.dia), "entrada", "E2", linha_origem=m.linha_origem)
        else:
            mover(m.unidade, m.sku, "entrada_transferencia" if m.q > 0 else "saida_transferencia", m.q, m.dia,
                  no_dia(m.dia), "entrada" if m.q > 0 else "saida", "E4", linha_origem=m.linha_origem,
                  transferencia=m.transferencia)
    for a in ajustes.itertuples():
        mover(a.unidade, a.sku, "ajuste", -a.q, a.dia, no_dia(a.dia, "23:59:59"), "ajuste", "E2", FONTE_CONTAGEM,
              motivo=MOTIVO_AJUSTE)
    for o in online.itertuples():
        for tipo, q, c in (("saida_realocacao", -o.q, "loja_fisica"), ("entrada_realocacao", o.q, "online")):
            mover(o.unidade, o.sku, tipo, q, FIM_DO_HISTORICO, no_dia(FIM_DO_HISTORICO, "23:59:59"), "realocacao", "E7",
                  FONTE_CONTAGEM, motivo=MOTIVO_ONLINE_PINHEIROS, canal_mov=c)

    mov = pd.DataFrame(linhas)
    mov = mov[mov["quantidade"] != 0]
    mov["variante"] = mov["sku"].map(dono_da_peca(mov["sku"], variantes))
    sem_variante = mov[~mov["variante"].isin(variantes)]
    for (unidade, sku), g in sem_variante.groupby(["unidade", "sku"]):
        saida.pendente("movimentacao_estoque", FONTE_CONTAGEM, None, "sku", sku,
                       f"peça sem variante: {int(g['quantidade'].sum())} no saldo de {unidade}")
    mov = mov[mov["variante"].isin(variantes)]
    mov = mov.sort_values(["dia", "grupo", "criado_em"], kind="stable").reset_index(drop=True)
    # V17: a cancelada sai e volta na hora, e a venda mostra que a peça estava na loja. O histórico fecha por dia
    # sem ela (E2); se naquele momento o saldo não tem a peça, faltou registrar uma entrada antes e uma saída
    # depois: o saldo inicial sobe e entra um ajuste de saída no fim do dia, como os da E2
    peca = ["variante", "unidade", "canal"]
    canceladas_sem_peca = 0
    while True:
        saldo = mov.groupby(peca)["quantidade"].cumsum()
        falta = (mov["grupo"] == GRUPO["cancelada"]) & (mov["tipo"] == "venda") & (saldo < 0)
        if not falta.any():
            break
        m, q = mov[falta].iloc[0], int(-saldo[falta].iloc[0])
        inicial = (mov[peca] == m[peca]).all(axis=1) & (mov["tipo"] == "saldo_inicial")
        if inicial.any():
            mov.loc[inicial.idxmax(), "quantidade"] += q
        else:
            mov.loc[len(mov)] = {**m, "tipo": "saldo_inicial", "quantidade": q, "motivo": MOTIVO_SALDO_INICIAL,
                                 "pedido": None, "dia": r.INICIO_HISTORICO, "criado_em": no_dia(r.INICIO_HISTORICO),
                                 "grupo": GRUPO["saldo_inicial"], "decisao": "E2", "fonte": FONTE_CONTAGEM,
                                 "linha_origem": None}
        mov.loc[len(mov)] = {**m, "tipo": "ajuste", "quantidade": -q, "motivo": MOTIVO_AJUSTE, "pedido": None,
                             "criado_em": no_dia(m["dia"], "23:59:59"), "grupo": GRUPO["ajuste"], "decisao": "V17"}
        mov = mov.sort_values(["dia", "grupo", "criado_em"], kind="stable").reset_index(drop=True)
        canceladas_sem_peca += 1
    mov["ordem"] = mov.index + 1
    mov["chave"] = "mov#" + mov["ordem"].astype(str)
    for m in mov.itertuples():
        saida.de_onde("movimentacao_estoque", m.chave, m.fonte, m.linha_origem, m.decisao)

    # transferências (E4): uma por envio ou recebimento sem par; a em trânsito em 01/10 fica enviada (E13)
    destino_em_transito = e["transito"].set_index("linha_origem")["destino"]
    transferencias, itens_t = [], []
    for chave, g in mov[mov["transferencia"].notna()].groupby("transferencia", sort=False):
        sai, entra = g[g["quantidade"] < 0].iloc[0], g[g["quantidade"] > 0]
        recebida = len(entra) > 0
        destino = entra["unidade"].iloc[0] if recebida else destino_em_transito[chave]
        transferencias.append(dict(chave=chave, unidade_origem=sai.unidade, unidade_destino=destino,
                                   status="recebida" if recebida else "enviada", solicitante=CONTA_MIGRACAO,
                                   enviado_por=CONTA_MIGRACAO, recebido_por=CONTA_MIGRACAO if recebida else None,
                                   solicitada_em=sai.criado_em, enviada_em=sai.criado_em,
                                   recebida_em=entra["criado_em"].iloc[0] if recebida else None))
        q = -int(sai.quantidade)
        itens_t.append(dict(transferencia=chave, variante=sai.variante, canal_saida=sai.canal,
                            canal_entrada=canal(destino), quantidade_solicitada=q, quantidade_enviada=q,
                            quantidade_recebida=q if recebida else None))
        for linha in g["linha_origem"].dropna().unique():
            saida.de_onde("transferencia", chave, FONTE_BANCO, linha, "E4" if recebida else "E13")

    # saldo de cada peça depois de cada movimentação: nunca negativo, e o último é o estoque em 01/10/2026
    mov["saldo"] = mov.groupby(peca)["quantidade"].cumsum()
    negativos = int((mov["saldo"] < 0).sum())
    saldo = mov.groupby(peca)["saldo"].last()
    # E14: no CD, o mínimo é a saída do mês com piso de 2; nas lojas, sem mínimo
    minimo = r.estoque_minimo_cd(movimentos)
    minimo = minimo.groupby(dono_da_peca(minimo.index, variantes)).sum()
    minimo.index = pd.MultiIndex.from_tuples([(v, r.CD, "online") for v in minimo.index], names=peca)
    estoque = pd.concat([saldo.rename("quantidade"), minimo.rename("estoque_minimo")], axis=1).reset_index()
    estoque["quantidade"] = estoque["quantidade"].fillna(0).astype(int)
    estoque["estoque_minimo"] = estoque["estoque_minimo"].astype("Int64")
    estoque = estoque[(estoque["quantidade"] > 0) | estoque["estoque_minimo"].notna()]
    estoque.insert(4, "quantidade_reservada", 0)  # E8

    colunas = ["ordem", "chave", "variante", "unidade", "canal", "tipo", "quantidade", "motivo", "pedido",
               "transferencia", "usuario", "criado_em"]
    saida.tabela("movimentacao_estoque", mov[colunas])
    saida.tabela("transferencia", pd.DataFrame(transferencias))
    saida.tabela("item_transferencia", pd.DataFrame(itens_t))
    saida.tabela("estoque", estoque)

    cd = estoque[estoque["unidade"] == r.CD]
    return dict(por_unidade=estoque.groupby(["unidade", "canal"])["quantidade"].sum(),
                em_transito=sum(i["quantidade_enviada"] for i in itens_t if i["quantidade_recebida"] is None),
                negativos=negativos, sem_variante=int(sem_variante["quantidade"].sum()),
                canceladas_sem_peca=canceladas_sem_peca,
                abaixo_do_minimo=int((cd["quantidade"] < cd["estoque_minimo"]).sum()),
                sem_minimo=int(((cd["quantidade"] > 0) & cd["estoque_minimo"].isna()).sum()))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--origem", type=Path, default=Path("staging_vulto"))
    p.add_argument("--destino", type=Path, default=Path("tratado_vulto"))
    args = p.parse_args()

    saida = Saida()
    tratar_unidades(args.origem, saida)
    tratar_contas_internas(args.origem, saida)
    tratar_catalogo(args.origem, saida)
    por_origem = tratar_clientes(args.origem, saida)
    vendas = tratar_vendas(args.origem, saida)
    estoque = tratar_estoque(args.origem, saida, vendas["original"])
    saida.gravar(args.destino)

    print("clientes por origem:", por_origem.to_dict())
    fat = faturamento_tratado(saida)
    print(f"faturamento dos pedidos: {fat.sum():,.2f} | devoluções sem pedido original: {vendas['sem_original']:,.2f}")
    nas_unidades = estoque["por_unidade"].sum()
    print("estoque por unidade e canal:", estoque["por_unidade"].to_dict())
    print(f"estoque em 01/10/2026: {nas_unidades:,} nas unidades + {estoque['em_transito']} em trânsito = "
          f"{nas_unidades + estoque['em_transito']:,} | saldos negativos no caminho: {estoque['negativos']} | "
          f"peças sem variante: {estoque['sem_variante']} | canceladas sem a peça no saldo (V17): "
          f"{estoque['canceladas_sem_peca']} | CD abaixo do mínimo: {estoque['abaixo_do_minimo']}, "
          f"sem mínimo: {estoque['sem_minimo']}")

    for nome, df in saida.tabelas.items():
        print(f"{nome}: {len(df)} linhas")
    print(f"origem_migracao: {len(saida.origem)} linhas | pendencias: {len(saida.pendencias)}")


if __name__ == "__main__":
    main()
