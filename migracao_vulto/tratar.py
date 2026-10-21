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


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--origem", type=Path, default=Path("staging_vulto"))
    p.add_argument("--destino", type=Path, default=Path("tratado_vulto"))
    args = p.parse_args()

    saida = Saida()
    tratar_unidades(args.origem, saida)
    tratar_contas_internas(args.origem, saida)
    saida.gravar(args.destino)

    for nome, df in saida.tabelas.items():
        print(f"{nome}: {len(df)} linhas")
    print(f"origem_migracao: {len(saida.origem)} linhas | pendencias: {len(saida.pendencias)}")


if __name__ == "__main__":
    main()
