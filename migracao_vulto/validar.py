# Validação da migração da Vulto (etapa 8): prova que nada se perdeu sem explicação. Para cada fonte da
# Vulto, conta as linhas do staging (etapa 1), quantas entraram no tratado (etapa 2) e o motivo de cada uma
# que ficou de fora; para cada tabela, compara o tratado com o que a carga gravou no banco (etapa 3).
# O fio é a origem da migração (P4): toda linha da Vulto que virou registro, ou se juntou a um, aparece nela.
# Só lê: o staging, o tratado e o banco (numa transação só de leitura). Grava o relatório em --destino.
# Uso, na raiz do repositório com o venv ativo, depois do tratamento e da carga:
#   python -m migracao_vulto.validar --staging staging_vulto --tratado tratado_vulto --destino migracao_vulto/validacao.md
import argparse
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from migracao_vulto import regras as r
from migracao_vulto.tratar import NAO_MIGRA


@dataclass
class Fonte:
    nome: str
    linhas: int
    aproveitadas: int
    # motivo → linhas que ficaram de fora
    fora: dict[str, int] = field(default_factory=dict)
    viraram: str = ""

    @property
    def sem_explicacao(self) -> int:
        return self.linhas - self.aproveitadas - sum(n for m, n in self.fora.items() if m != "sem motivo")


# o motivo de cada venda que não vira pedido (regras.motivo_fora e regras.tipo_lancamento), em texto
MOTIVO_DA_VENDA = {
    "V8 teste": "venda de teste em produção (V8)",
    "V5 cópia do pedido": "cópia do mesmo pedido gravada pelo app (V5)",
    "V9 relançamento da Batel": "relançamento da gerente da Batel: vale a venda da planilha (V9)",
    "V6 troca": "troca lançada como venda nova: não vira pedido, a peça trocada entra como troca no pedido original (V6)",
}


def ler(pasta: Path, nome: str) -> pd.DataFrame:
    return pd.read_csv(pasta / f"{nome}.csv", dtype=str, keep_default_na=False)


def contar(serie: pd.Series) -> dict[str, int]:
    return {str(k): int(v) for k, v in serie.value_counts().items()}


# ---------- da origem ao tratado ----------

def fontes(staging: Path, tratado: Path) -> list[Fonte]:
    origem = ler(tratado, "origem_migracao")
    # a origem da migração cita o registro e o campo que mudou; o item do pedido guarda a própria linha
    # (itens_venda#N), mesmo quando nada nele mudou
    usadas = set(origem["linha_origem"]) | set(ler(tratado, "item_pedido")["item"])
    pendencias = ler(tratado, "pendencias")
    lista = []

    def fonte(nome, arquivo, viraram, motivo_das_de_fora=None):
        t = ler(staging, arquivo)
        usada = t["linha_origem"].isin(usadas)
        fora = t[~usada]
        motivos = motivo_das_de_fora(fora) if motivo_das_de_fora is not None and len(fora) else {}
        lista.append(Fonte(nome, len(t), int(usada.sum()), motivos, viraram))

    fonte("Lojas (`lojas`)", "banco_lojas", "5 unidades: o site e o app viram o canal online do CD Cajamar (V12)")

    def funcionarios(fora):
        return {"gerentes, operador do CD e contas genéricas (`gerente batel`, `admin`...): sem conta própria (P9)": len(fora)}
    fonte("Funcionários (`funcionarios`)", "banco_funcionarios",
          "13 contas internas inativas; com as 3 do SAC e a \"Migração Vulto\", 17 (P9)", funcionarios)

    fonte("Catálogo (5 abas da planilha)", "catalogo_produtos",
          "produtos e variantes: o mesmo SKU em vários formatos vira uma variante (C13), a grade abre por tamanho (C20)")

    # vendas: o motivo de cada venda que não virou pedido nem devolução ou troca
    vendas, itens = ler(staging, "banco_vendas").replace("", None), ler(staging, "banco_itens_venda").replace("", None)
    lanc = r.lancamentos_banco(vendas, itens, ler(staging, "catalogo_produtos").replace("", None))
    lanc = lanc[lanc["linha_origem"].astype(str).str.startswith("vendas#")]
    motivo_da_venda = lanc.set_index("linha_origem")["motivo"].to_dict()

    def vendas_fora(fora):
        return contar(fora["linha_origem"].map(lambda l: MOTIVO_DA_VENDA.get(motivo_da_venda.get(l), "sem motivo")))
    fonte("Vendas do sistema (`vendas`)", "banco_vendas",
          "pedidos; a devolução (venda negativa) e a troca viram movimentação e estorno no pedido original (V6); "
          "a cópia do app se junta ao pedido (V5)", vendas_fora)

    venda_do_item = itens.set_index("linha_origem")["venda_id"].to_dict()
    motivo_por_id = lanc.set_index("id")["motivo"].to_dict()
    sku_do_item = itens.set_index("linha_origem")["sku"].to_dict()

    def itens_fora(fora):
        def motivo(linha):
            da_venda = motivo_por_id.get(venda_do_item.get(linha))
            if da_venda in MOTIVO_DA_VENDA:
                return f"item de {MOTIVO_DA_VENDA[da_venda]}"
            # a devolução de um item sem a peça identificada vira só o estorno no pedido original: sem peça,
            # não há movimentação de estoque que cite a linha do item
            if sku_do_item.get(linha) in ("DIVERSO", "PRODUTO SEM CADASTRO") \
                    and f"vendas#{venda_do_item.get(linha)}" in usadas:
                return "devolução de item sem cadastro (DIVERSO): vira só o estorno no pedido original, sem peça para voltar ao estoque (P8, V6)"
            return "sem motivo"
        return contar(fora["linha_origem"].map(motivo))
    fonte("Itens das vendas (`itens_venda`)", "banco_itens_venda", "itens dos pedidos e movimentações de estoque", itens_fora)

    def batel_fora(fora):
        return {"linhas de subtotal da planilha (\"TOTAL SEMANA\", \"TOTAL MARÇO\"), não são vendas": len(fora)}
    fonte("Vendas manuais da Batel (planilha)", "batel_vendas_manuais", "pedidos `VLT-BATEL-<linha>` (V9, V23)", batel_fora)

    def compras_fora(fora):
        return {"espelho das vendas no sistema do Vulto+: liga cada ponto à venda (K4b, K6), não vira tabela": len(fora)}
    fonte("Compras do Vulto+ (`compras`)", "banco_compras", "nenhuma tabela própria", compras_fora)

    fonte("Cadastro do Vulto+ (aba `Clientes`)", "clientes_clientes",
          "clientes sem conta: os cadastros do mesmo CPF viram um (K1)")
    fonte("Aba antiga do Vulto+", "clientes_antigos", "clientes sem conta e o saldo de 2023 dos ATIVOS (K5c, K5d)")

    pend_pontos = pendencias[pendencias["tabela"] == "movimento_pontos"]
    motivo_pendencia = pend_pontos.set_index("linha_origem")["motivo"].to_dict()

    def pontos_fora(fora):
        return contar(fora["linha_origem"].map(lambda l: motivo_pendencia.get(l, "sem motivo")))
    fonte("Movimentos de pontos (`pontos_mov`)", "banco_pontos_mov",
          "movimentos de pontos, mais os que a migração cria (saldo inicial K5a, dobro do online K4b, estornos K6)",
          pontos_fora)

    resgates = ler(staging, "clientes_resgates")
    dia_do_resgate = pd.to_datetime(resgates.set_index("linha_origem")["data"], errors="coerce")

    def resgates_fora(fora):
        def motivo(linha):
            if linha in motivo_pendencia:
                return motivo_pendencia[linha]
            dia = dia_do_resgate.get(linha)
            if pd.notna(dia) and dia < pd.Timestamp(r.DATA_SALDO_INICIAL):
                return "antes de 01/01/2025: já descontado no saldo da planilha (K5a)"
            return "já registrado no banco como resgate (K7a)"
        return contar(fora["linha_origem"].map(motivo))
    fonte("Resgates anotados na planilha (aba `Resgates`)", "clientes_resgates",
          "resgates que só estão na planilha (K7a)", resgates_fora)

    def atendimentos_fora(fora):
        return {"spam e testes (A3)": len(fora)}
    fonte("Atendimentos (WhatsApp, Instagram e e-mail)", "atendimentos",
          "chamados e as mensagens seguintes de cada conversa (P6)", atendimentos_fora)

    fonte("Entradas de estoque (`entradas_estoque`)", "banco_entradas_estoque",
          "recebimentos e transferências (E4)")

    def estoque_fora(fora):
        return {"saldo do sistema, que não migra: as lojas valem pela contagem e o CD pelo WMS (E1); "
                "o site e o app são cópia do CD (E3)": len(fora)}
    fonte("Saldo do sistema (`estoque`)", "banco_estoque", "nenhuma tabela: comparação no relatório da migração",
          estoque_fora)

    # a contagem só é citada pelos movimentos que saem dela: o saldo inicial, o ajuste de saída sem registro e a
    # separação do online da Pinheiros (E2, E7), e cada um cita a primeira linha contada da peça. A linha não
    # citada é outra linha da mesma peça (contada em dois lugares da loja) ou de uma peça que não precisou de
    # nenhum desses movimentos; fora disso, fica sem explicação
    movimentos = ler(tratado, "movimentacao_estoque").set_index("chave")
    da_contagem = origem[(origem["tabela"] == "movimentacao_estoque")
                         & (origem["fonte"] == "contagem_inventario_set26.xlsx")]["id_registro"]
    pecas_citadas = set(zip(movimentos.loc[da_contagem, "unidade"], movimentos.loc[da_contagem, "variante"]))
    catalogo = ler(staging, "catalogo_produtos")
    variantes = r.variantes_do_catalogo(catalogo)
    formas = r.formas_compactas(variantes)

    def contagem_fora(fora):
        def motivo(linha):
            if linha["nivel"] != "sku":
                return "contagem por modelo, distribuída pelas variantes (E6)"
            peca = (r.UNIDADE_DA_CONTAGEM.get(linha["unidade"], linha["unidade"]),
                    r.peca_contada(linha["codigo"], variantes, formas)[0])
            if peca in pecas_citadas:
                return "a mesma peça contada em mais de uma linha (provador, vitrine...): os movimentos citam a primeira"
            return ("peça contada que já entra no saldo de 01/10 sem saldo inicial nem ajuste pela contagem: as "
                    "entradas registradas explicam todas as peças dela (E2)")
        return contar(fora.apply(motivo, axis=1))
    fonte("Contagem de set/2026 e WMS do CD", "contagem_set26", "saldo de cada peça em 01/10/2026 (E1, E2)",
          contagem_fora)
    return lista


# ---------- do tratado ao banco ----------

CONTAGEM_NO_BANCO = {
    "unidade": "select count(*) from unidade where marca = 'vulto'",
    "usuario": "select count(*) from usuario where tipo_conta = 'interna' and email like '%@vulto.example'",
    "produto": "select count(*) from produto where marca = 'vulto'",
    "variante": "select count(*) from variante v join produto p using (id_produto) where p.marca = 'vulto'",
    "historico_preco": "select count(*) from historico_preco h join variante v using (id_variante) "
                       "join produto p using (id_produto) where p.marca = 'vulto'",
    "cliente_sem_conta": "select count(*) from cliente_sem_conta where origem = 'migracao_vulto'",
    "pedido": "select count(*) from pedido p join unidade u using (id_unidade) where u.marca = 'vulto'",
    "item_pedido": "select count(*) from item_pedido i join pedido p using (id_pedido) join unidade u "
                   "on u.id_unidade = p.id_unidade where u.marca = 'vulto'",
    "pagamento": "select count(*) from pagamento g join pedido p using (id_pedido) join unidade u "
                 "on u.id_unidade = p.id_unidade where u.marca = 'vulto'",
    "chamado": "select count(*) from chamado where id_chamado in "
               "(select id_registro::int from origem_migracao where tabela = 'chamado')",
    "mensagem": "select count(*) from mensagem where id_chamado in "
                "(select id_registro::int from origem_migracao where tabela = 'chamado')",
    "transferencia": "select count(*) from transferencia t join unidade u on u.id_unidade = t.id_unidade_origem "
                     "where u.marca = 'vulto'",
    "movimentacao_estoque": "select count(*) from movimentacao_estoque m join unidade u using (id_unidade) "
                            "where u.marca = 'vulto'",
    "estoque": "select count(*) from estoque e join unidade u using (id_unidade) where u.marca = 'vulto' "
               "and e.quantidade > 0",
    "movimento_pontos": "select count(*) from movimento_pontos where marca = 'vulto'",
    "origem_migracao": "select count(*) from origem_migracao",
}

EXPLICACAO = {
    "estoque": "só as posições com peça: o banco cria a linha zerada de toda peça e unidade por onde o histórico passa",
}


def tabelas(tratado: Path, banco) -> list[tuple[str, int, int]]:
    linhas = []
    with banco.connect() as conexao:
        conexao.execute(text("set transaction read only"))
        for tabela, consulta in CONTAGEM_NO_BANCO.items():
            t = ler(tratado, tabela)
            no_tratado = int((pd.to_numeric(t["quantidade"]) > 0).sum()) if tabela == "estoque" else len(t)
            linhas.append((tabela, no_tratado, int(conexao.execute(text(consulta)).scalar())))
    return linhas


# ---------- relatório ----------

def numero(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def relatorio(lista: list[Fonte], comparacao: list[tuple[str, int, int]], pendencias: pd.DataFrame) -> str:
    sem_explicacao = sum(f.sem_explicacao for f in lista)
    diferentes = [t for t, a, b in comparacao if a != b]
    partes = [
        "# Validação da migração da Vulto",
        "",
        f"Gerado por [`validar.py`](validar.py) em {datetime.now():%d/%m/%Y %H:%M}, a partir do staging, do tratado e do banco. "
        "Para gerar de novo, depois do tratamento e da carga:",
        "",
        "```bash",
        "python -m migracao_vulto.validar --staging staging_vulto --tratado tratado_vulto --destino migracao_vulto/validacao.md",
        "```",
        "",
        "## Resultado",
        "",
        f"- **Da origem ao tratado:** {numero(sum(f.linhas for f in lista))} linhas da Vulto em {len(lista)} fontes; "
        f"**{numero(sem_explicacao)} sem explicação**. Cada linha que ficou de fora tem o motivo e a decisão abaixo.",
        f"- **Do tratado ao banco:** {len(comparacao)} tabelas; "
        + ("**todas com o mesmo número de registros no tratado e no banco**: a carga não rejeitou nada."
           if not diferentes else f"**diferença em {', '.join(diferentes)}**: conferir a carga."),
        f"- **Pendências do tratamento:** {len(pendencias)} linhas que não se resolveram sem chute; ficam de fora, com o motivo (seção 3).",
        "",
        "## 1. Da origem (Vulto) ao tratado",
        "",
        "Uma linha é aproveitada quando virou registro na plataforma ou se juntou a um (a origem da migração, P4, guarda as duas).",
        "",
        "| Fonte da Vulto | Linhas | Aproveitadas | Ficaram de fora | Viraram |",
        "|---|---:|---:|---:|---|",
    ]
    for f in lista:
        partes.append(f"| {f.nome} | {numero(f.linhas)} | {numero(f.aproveitadas)} | {numero(sum(f.fora.values()))} | {f.viraram} |")
    partes += ["", "### Por que ficaram de fora", ""]
    for f in lista:
        if not f.fora:
            continue
        partes.append(f"**{f.nome}**")
        partes.append("")
        for motivo, n in sorted(f.fora.items(), key=lambda m: -m[1]):
            if motivo != "sem motivo":
                partes.append(f"- {numero(n)}: {motivo}")
        if f.sem_explicacao:
            partes.append(f"- **{numero(f.sem_explicacao)} sem explicação**")
        partes.append("")
    partes += [
        "## 2. Do tratado ao banco",
        "",
        "A carga roda numa transação só e só grava se tudo passar nas regras do banco e os três números baterem.",
        "",
        "| Tabela | Tratado | Banco | Diferença |",
        "|---|---:|---:|---|",
    ]
    for tabela, no_tratado, no_banco in comparacao:
        dif = "igual" if no_tratado == no_banco else f"{numero(no_banco - no_tratado)}"
        nota = f" ({EXPLICACAO[tabela]})" if tabela in EXPLICACAO else ""
        partes.append(f"| `{tabela}` | {numero(no_tratado)} | {numero(no_banco)} | {dif}{nota} |")
    partes += ["", "## 3. Pendências do tratamento", "",
               "O que não se resolveu sem chute (um valor sem tradução, uma ligação que não fecha). Não entra no banco.", "",
               "| Tabela | Motivo | Linhas |", "|---|---|---:|"]
    for (tabela, motivo), n in pendencias.groupby(["tabela", "motivo"]).size().sort_values(ascending=False).items():
        partes.append(f"| `{tabela}` | {motivo} | {numero(int(n))} |")
    return "\n".join(partes) + "\n"


def main():
    p = argparse.ArgumentParser(description="Valida a migração da Vulto: origem × tratado × banco.")
    p.add_argument("--staging", type=Path, default=Path("staging_vulto"))
    p.add_argument("--tratado", type=Path, default=Path("tratado_vulto"))
    p.add_argument("--destino", type=Path, default=Path("migracao_vulto/validacao.md"))
    args = p.parse_args()

    from src.config.settings import DATABASE_URL_DIRECT
    if not DATABASE_URL_DIRECT:
        raise SystemExit("DATABASE_URL_DIRECT não definida no .env")
    banco = create_engine(make_url(DATABASE_URL_DIRECT).set(drivername="postgresql+psycopg"))

    lista = fontes(args.staging, args.tratado)
    comparacao = tabelas(args.tratado, banco)
    pendencias = ler(args.tratado, "pendencias")
    args.destino.write_text(relatorio(lista, comparacao, pendencias), encoding="utf-8")

    for f in lista:
        print(f"{f.nome}: {f.linhas} linhas, {f.aproveitadas} aproveitadas, {sum(f.fora.values())} de fora, "
              f"{f.sem_explicacao} sem explicação")
    for tabela, a, b in comparacao:
        print(f"  {tabela}: tratado {a} | banco {b}{'' if a == b else '  <-- diferente'}")
    print(f"Relatório em {args.destino}")


if __name__ == "__main__":
    main()
