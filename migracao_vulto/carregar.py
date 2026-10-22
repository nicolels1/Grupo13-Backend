# Carga dos dados da Vulto (etapa 3): grava no banco da Casa Lorenzi as tabelas do tratamento (etapa 2), numa
# transação só, e confere os três números calculados pelo próprio banco contra os do numeros.md.
# Cada registro do tratado tem uma chave natural (nome da unidade, e-mail da conta, SKU, código da venda,
# chave pts#n...). A carga reserva os ids na sequência de cada tabela, troca toda chave pelo id e copia as
# linhas com COPY, na ordem em que os triggers precisam delas (saldo do estoque e dos pontos, ADR 0005).
# Por último grava a origem_migracao com o id do banco no lugar da chave (P4, V13).
#
# Ensaio (o padrão): carrega tudo, confere os números e desfaz (ROLLBACK); nada fica gravado. As 17 contas
# internas da Vulto (P9) entram direto em auth.users, dentro da transação, para serem desfeitas junto.
# Carga definitiva (--confirmar): cria as 17 contas no Supabase Auth sem senha e bloqueadas e só grava
# (COMMIT) se os três números baterem; se não baterem, desfaz e apaga as contas criadas. Não roda duas vezes: se a Vulto já está no banco, para antes de começar.
# Uso, na raiz do repositório com o venv ativo (precisa de DATABASE_URL_DIRECT no .env, e na carga
# definitiva também de SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY):
#   python -m migracao_vulto.carregar --origem tratado_vulto
#   python -m migracao_vulto.carregar --origem tratado_vulto --confirmar
import argparse
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from migracao_vulto.tratar import CONTA_MIGRACAO

BRASILIA = ZoneInfo("America/Sao_Paulo")
# fora da carga: são listas para a equipe (o que ficou de fora por decisão, e as peças para a Marina revisar)
NAO_CARREGA = ("pendencias", "lista_marina")

# os três números do numeros.md: o banco tem que chegar neles depois da carga
INICIO_FATURAMENTO = datetime(2025, 1, 1, tzinfo=BRASILIA)
DATA_BASE = datetime(2026, 10, 1, tzinfo=BRASILIA)
ESPERADO = {
    "faturamento jan/25–set/26 (R$)": Decimal("6438443.35"),
    "estoque em 01/10/2026 (peças)": 15233,
    "passivo em 01/10/2026 (pontos)": 9436136,
    "passivo em 01/10/2026 (R$)": Decimal("652718.10"),
}


class ErroCarga(Exception):
    pass


# ---------- chaves naturais → ids ----------

class Chaves:
    """O id que o banco deu (ou vai dar) a cada chave natural, por tabela."""

    def __init__(self):
        self.por_tabela: dict[str, dict] = {}

    def guardar(self, tabela: str, chaves, ids):
        self.por_tabela.setdefault(tabela, {}).update(zip(chaves, ids))

    # troca a chave pelo id; chave que não está na tabela é erro (nada é ligado no chute)
    def trocar(self, tabela: str, valores: pd.Series, coluna: str = "") -> list:
        mapa = self.por_tabela.get(tabela, {})
        faltam = [v for v in valores if v is not None and v not in mapa]
        if faltam:
            raise ErroCarga(f"{coluna or tabela}: {len(faltam)} chave(s) sem registro em {tabela}, ex.: {faltam[0]}")
        return [None if v is None else mapa[v] for v in valores]


@dataclass
class Tabela:
    nome: str                       # tabela do banco e nome do CSV
    pk: str | None = None           # coluna do id; sem ela o banco gera o id e ninguém aponta para o registro
    chave: str | None = None        # coluna do CSV com a chave natural
    ligacoes: dict = field(default_factory=dict)  # coluna do CSV → (coluna do banco, tabela de onde vem o id)
    fora: tuple = ()                # colunas do CSV que não vão para o banco
    inteiros: tuple = ()            # colunas inteiras que o CSV grava como 2.0
    ordenar: Callable[[pd.DataFrame], pd.DataFrame] | None = None  # ordem em que os triggers precisam das linhas


def pela_ordem(df: pd.DataFrame) -> pd.DataFrame:
    return df.sort_values("ordem", key=lambda c: c.astype(int), kind="stable")


# o estorno precisa do pagamento original já gravado (o trigger confere o método, P7)
def originais_primeiro(df: pd.DataFrame) -> pd.DataFrame:
    return df.sort_values("pagamento_original", key=lambda c: c.notna(), kind="stable")


def chave_do_cliente(df: pd.DataFrame) -> pd.Series:
    """O cliente é achado pelo CPF e, de quem não tem CPF, pelo e-mail (P2)."""
    return df["cpf"].where(df["cpf"].notna(), df["email"])


USUARIO = "usuario"
# na ordem das ligações: cada tabela só aponta para as de cima
TABELAS = [
    Tabela("unidade", "id_unidade", "nome"),
    Tabela("produto", "id_produto", "codigo", {"categoria": ("id_categoria", "categoria_produto")}, fora=("codigo",)),
    Tabela("variante", "id_variante", "sku", {"produto": ("id_produto", "produto")}, fora=("estoque_01_10",)),
    Tabela("historico_preco", ligacoes={"sku": ("id_variante", "variante")}, fora=("decisao",)),
    Tabela("cliente_sem_conta", "id_cliente_sem_conta", "chave"),
    Tabela("pedido", "id_pedido", "codigo_venda", {
        "cliente": ("id_cliente_sem_conta", "cliente_sem_conta"), "unidade": ("id_unidade", "unidade"),
        "registrado_por": ("id_registrado_por", USUARIO), "cancelado_por": ("id_cancelado_por", USUARIO),
    }),
    Tabela("item_pedido", "id_item", "item", {"pedido": ("id_pedido", "pedido"), "variante": ("id_variante", "variante")},
           fora=("item",)),
    Tabela("chamado", "id_chamado", "chave", {
        "cliente": ("id_cliente_sem_conta", "cliente_sem_conta"), "pedido": ("id_pedido", "pedido"),
        "responsavel": ("id_responsavel", USUARIO),
    }, fora=("chave",)),
    Tabela("mensagem", "id_mensagem", "chave", {"chamado": ("id_chamado", "chamado"), "autor": ("id_autor", USUARIO)},
           fora=("chave",)),
    Tabela("pagamento", "id_pagamento", "chave", {
        "pedido": ("id_pedido", "pedido"), "pagamento_original": ("id_pagamento_original", "pagamento"),
        "chamado": ("id_chamado", "chamado"),
    }, fora=("chave",), ordenar=originais_primeiro),
    Tabela("transferencia", "id_transferencia", "chave", {
        "unidade_origem": ("id_unidade_origem", "unidade"), "unidade_destino": ("id_unidade_destino", "unidade"),
        "solicitante": ("id_solicitante", USUARIO), "enviado_por": ("id_enviado_por", USUARIO),
        "recebido_por": ("id_recebido_por", USUARIO),
    }, fora=("chave",)),
    Tabela("item_transferencia", ligacoes={
        "transferencia": ("id_transferencia", "transferencia"), "variante": ("id_variante", "variante"),
    }, inteiros=("quantidade_solicitada", "quantidade_enviada", "quantidade_recebida")),
    Tabela("movimentacao_estoque", "id_movimentacao", "chave", {
        "variante": ("id_variante", "variante"), "unidade": ("id_unidade", "unidade"), "pedido": ("id_pedido", "pedido"),
        "transferencia": ("id_transferencia", "transferencia"), "usuario": ("id_usuario", USUARIO),
    }, fora=("ordem", "chave"), ordenar=pela_ordem),
    Tabela("movimento_pontos", "id_movimento", "chave", {
        "cliente": ("id_cliente_sem_conta", "cliente_sem_conta"), "pedido": ("id_pedido", "pedido"),
        "movimento_original": ("id_movimento_original", "movimento_pontos"),
        "registrado_por": ("id_registrado_por", USUARIO),
    }, fora=("ordem", "chave"), ordenar=pela_ordem),
]


def ler(origem: Path, nome: str) -> pd.DataFrame:
    """CSV do tratado com tudo em texto; vazio vira None (NULL no banco)."""
    df = pd.read_csv(origem / f"{nome}.csv", dtype=str, keep_default_na=False)
    return df.astype(object).where(df != "", None)


def montar(tabela: Tabela, df: pd.DataFrame, chaves: Chaves, ids: list | None = None) -> pd.DataFrame:
    """Linhas prontas para o COPY: id reservado, chaves trocadas pelos ids, na ordem dos triggers.
    Os ids são guardados antes das ligações, para a tabela poder apontar para ela mesma (estorno)."""
    if tabela.ordenar is not None:
        df = tabela.ordenar(df)
    df = df.reset_index(drop=True)
    if tabela.pk is not None:
        chaves.guardar(tabela.nome, df[tabela.chave], ids)
    saida = pd.DataFrame(index=df.index)
    if tabela.pk is not None:
        saida[tabela.pk] = ids
    for coluna in df.columns:
        if coluna in tabela.fora:
            continue
        if coluna in tabela.ligacoes:
            destino, referida = tabela.ligacoes[coluna]
            saida[destino] = pd.Series(chaves.trocar(referida, df[coluna], f"{tabela.nome}.{coluna}"),
                                       index=df.index, dtype=object)
        elif coluna in tabela.inteiros:
            saida[coluna] = pd.Series([None if v is None else int(float(v)) for v in df[coluna]], index=df.index,
                                      dtype=object)
        else:
            saida[coluna] = df[coluna]
    return saida


def montar_origem(df: pd.DataFrame, chaves: Chaves) -> pd.DataFrame:
    """origem_migracao com o id do banco no lugar da chave natural (V13: a plataforma acha pelo id)."""
    df = df.copy()
    ids = []
    for tabela, chave in zip(df["tabela"], df["id_registro"]):
        mapa = chaves.por_tabela.get(tabela, {})
        if chave not in mapa:
            raise ErroCarga(f"origem_migracao: {tabela} {chave} não foi carregado")
        ids.append(str(mapa[chave]))
    df["id_registro"] = ids
    return df


def diferencas(numeros: dict, esperado: dict = ESPERADO) -> list[str]:
    return [nome for nome, valor in esperado.items() if numeros.get(nome) != valor]


# ---------- banco ----------

def _psycopg(db: Session):
    return db.connection().connection.driver_connection


def reservar(db: Session, tabela: str, pk: str, n: int) -> list[int]:
    """n ids seguidos da sequência da tabela. A sequência não volta no ROLLBACK: o ensaio realinha no fim."""
    if n == 0:
        return []
    seq = db.scalar(text("SELECT pg_get_serial_sequence(:t, :c)"), {"t": f"public.{tabela}", "c": pk})
    primeiro = db.scalar(text("SELECT nextval(:s)"), {"s": seq})
    db.execute(text("SELECT setval(:s, :ultimo)"), {"s": seq, "ultimo": primeiro + n - 1})
    return list(range(primeiro, primeiro + n))


def copiar(db: Session, tabela: str, df: pd.DataFrame):
    """COPY na ordem das linhas: os triggers de linha rodam nessa ordem."""
    if df.empty:
        return
    colunas = ", ".join(df.columns)
    with _psycopg(db).cursor() as cur, cur.copy(f"COPY public.{tabela} ({colunas}) FROM STDIN") as copy:
        for linha in df.itertuples(index=False, name=None):
            copy.write_row([_valor(v) for v in linha])


# o pandas guarda id com vazio na coluna como float (81.0): volta a ser inteiro, e NaN vira NULL
def _valor(v):
    if isinstance(v, float):
        return None if pd.isna(v) else int(v) if v.is_integer() else v
    return v


def ja_carregada(db: Session) -> bool:
    return bool(db.scalar(text("SELECT EXISTS (SELECT 1 FROM unidade WHERE marca = 'vulto')")))


def logins_do_ensaio(db: Session, emails: list[str]) -> dict:
    """No ensaio as contas entram direto em auth.users, na mesma transação, e somem no ROLLBACK."""
    logins = {email: uuid.uuid4() for email in emails}
    for email, id_usuario in logins.items():
        db.execute(text(
            "INSERT INTO auth.users (instance_id, id, aud, role, email, email_confirmed_at, banned_until, "
            "raw_app_meta_data, raw_user_meta_data, created_at, updated_at) "
            "VALUES ('00000000-0000-0000-0000-000000000000', :id, 'authenticated', 'authenticated', :email, now(), "
            "now() + interval '100 years', '{\"provider\": \"email\", \"providers\": [\"email\"]}', '{}', now(), now())"
        ), {"id": id_usuario, "email": email})
    return logins


def logins_definitivos(db: Session, auth, emails: list[str]) -> tuple[dict, list]:
    """Contas no Supabase Auth sem senha e bloqueadas (P9). Devolve também as criadas agora, para apagar se a
    carga não for gravada. Login que já existe com o e-mail é reaproveitado."""
    logins, criados = {}, []
    for email in emails:
        existente = db.scalar(text("SELECT login_por_email(:email)"), {"email": email})
        if existente is None:
            existente = auth.criar_login_bloqueado(email)
            criados.append(existente)
        logins[email] = existente
    db.rollback()  # a consulta acima abriu uma transação; a carga começa noutra
    return logins, criados


def _passo(nome: str, linhas: int, inicio: float):
    print(f"  {nome}: {linhas} linhas ({time.monotonic() - inicio:.0f}s)")


def carregar(db: Session, origem: Path, logins: dict) -> Chaves:
    chaves = Chaves()
    inicio = time.monotonic()

    # contas internas (P9): o id é o do login; modelo de acesso e unidade vêm do banco e da carga
    chaves.guardar("modelo_acesso", *zip(*db.execute(text("SELECT nome, id_modelo FROM modelo_acesso")).all()))
    chaves.guardar(USUARIO, logins.keys(), logins.values())

    for tabela in TABELAS:
        df = ler(origem, tabela.nome)
        if tabela.nome == "cliente_sem_conta":
            df["chave"] = chave_do_cliente(df)
        if tabela.nome == "produto":
            categorias(db, ler(origem, "categoria_produto"), chaves)
        ids = reservar(db, tabela.nome, tabela.pk, len(df)) if tabela.pk else None
        linhas = montar(tabela, df, chaves, ids)
        if tabela.nome == "cliente_sem_conta":
            # sem data, o banco preenche (120 só da aba antiga): a coluna fica fora do COPY dessas linhas
            linhas = linhas.drop(columns="chave")
            sem_data = linhas["criado_em"].isna()
            copiar(db, tabela.nome, linhas[~sem_data])
            copiar(db, tabela.nome, linhas[sem_data].drop(columns="criado_em"))
        else:
            copiar(db, tabela.nome, linhas)
        _passo(tabela.nome, len(linhas), inicio)
        if tabela.nome == "unidade":
            usuarios(db, ler(origem, USUARIO), chaves)
            _passo(USUARIO, len(logins), inicio)

    estoque_minimo(db, ler(origem, "estoque"), chaves, logins)
    _passo("estoque mínimo", 0, inicio)
    origem_df = montar_origem(ler(origem, "origem_migracao"), chaves)
    copiar(db, "origem_migracao", origem_df)
    _passo("origem_migracao", len(origem_df), inicio)
    return chaves


def usuarios(db: Session, df: pd.DataFrame, chaves: Chaves):
    linhas = pd.DataFrame({
        "id_usuario": chaves.trocar(USUARIO, df["email"]), "nome": df["nome"], "email": df["email"], "cpf": df["cpf"],
        "tipo_conta": df["tipo_conta"], "status_conta": df["status_conta"],
        "id_modelo_acesso": chaves.trocar("modelo_acesso", df["modelo_acesso"], "usuario.modelo_acesso"),
        "id_unidade": chaves.trocar("unidade", df["unidade"], "usuario.unidade"),
    })
    copiar(db, USUARIO, linhas)


def categorias(db: Session, df: pd.DataFrame, chaves: Chaves):
    """A Vulto usa categorias que a Lorenzi já tem (achadas pelo nome) e cria as que faltam."""
    chaves.guardar("categoria_produto", *zip(*db.execute(text("SELECT nome, id_categoria FROM categoria_produto")).all()))
    novas = df[~df["nome"].isin(chaves.por_tabela["categoria_produto"].keys())].reset_index(drop=True)
    ids = reservar(db, "categoria_produto", "id_categoria", len(novas))
    chaves.guardar("categoria_produto", novas["nome"], ids)
    copiar(db, "categoria_produto", novas.assign(id_categoria=ids))


def estoque_minimo(db: Session, df: pd.DataFrame, chaves: Chaves, logins: dict):
    """O saldo nasceu das movimentações (ADR 0005); do estoque.csv só se grava o mínimo."""
    df = df[df["estoque_minimo"].notna()]
    db.execute(text("CREATE TEMP TABLE minimo_vulto (id_variante int, id_unidade int, canal text, minimo int) "
                    "ON COMMIT DROP"))
    with _psycopg(db).cursor() as cur, cur.copy("COPY minimo_vulto FROM STDIN") as copy:
        for variante, unidade, canal, minimo in zip(chaves.trocar("variante", df["variante"]),
                                                    chaves.trocar("unidade", df["unidade"]),
                                                    df["canal"], df["estoque_minimo"]):
            copy.write_row([variante, unidade, canal, int(float(minimo))])
    db.execute(text(
        "UPDATE estoque e SET estoque_minimo = m.minimo, minimo_alterado_por = :conta, minimo_alterado_em = now() "
        "FROM minimo_vulto m WHERE e.id_variante = m.id_variante AND e.id_unidade = m.id_unidade AND e.canal = m.canal"
    ), {"conta": logins[CONTA_MIGRACAO]})


# ---------- conferência ----------

def conferir_estoque(db: Session, origem: Path, chaves: Chaves) -> int:
    """Saldo de cada variante, unidade e canal no banco contra o estoque.csv: devolve quantos diferem."""
    esperado = ler(origem, "estoque")
    esperado = {(v, u, c): int(q) for v, u, c, q in zip(
        chaves.trocar("variante", esperado["variante"]), chaves.trocar("unidade", esperado["unidade"]),
        esperado["canal"], esperado["quantidade"])}
    unidades = list(set(chaves.por_tabela["unidade"].values()))
    no_banco = {(v, u, c): q for v, u, c, q in db.execute(text(
        "SELECT id_variante, id_unidade, canal, quantidade FROM estoque WHERE id_unidade = ANY(:u)"),
        {"u": unidades}).all()}
    return sum(no_banco.get(k, 0) != q for k, q in esperado.items()) + sum(
        q != 0 for k, q in no_banco.items() if k not in esperado)


def calcular_numeros(db: Session) -> dict:
    """Os três números pelas consultas da própria plataforma (visão geral, histórico de estoque, passivo)."""
    from src.repositories import estoque_repository, fidelidade_repository, resumo_repository

    faturamento = sum((linha["valor"] for linha in resumo_repository.faturamento_por_mes(
        db, INICIO_FATURAMENTO, DATA_BASE, None, "vulto")), Decimal(0))
    vulto = set(db.scalars(text("SELECT id_unidade FROM unidade WHERE marca = 'vulto'")))
    nas_unidades = sum(linha["quantidade"] for linha in db.execute(estoque_repository.consulta_historico(DATA_BASE))
                       .mappings() if linha["id_unidade"] in vulto)
    em_transito = sum(linha["quantidade"] for linha in estoque_repository.listar_em_transito(db, DATA_BASE)
                      if linha["id_unidade_destino"] in vulto)
    passivo = [p for p in fidelidade_repository.passivo_por_valor(db, DATA_BASE) if p["marca"] == "vulto"]
    return {
        "faturamento jan/25–set/26 (R$)": faturamento,
        "estoque em 01/10/2026 (peças)": int(nas_unidades + em_transito),
        "passivo em 01/10/2026 (pontos)": int(sum(p["pontos"] for p in passivo)),
        "passivo em 01/10/2026 (R$)": sum((p["pontos"] * p["valor_ponto"] for p in passivo), Decimal(0))
        .quantize(Decimal("0.01")),
    }


def mostrar(numeros: dict, saldos_diferentes: int):
    print("\nConferência com o numeros.md:")
    for nome, esperado in ESPERADO.items():
        marca = "ok" if numeros[nome] == esperado else "DIFERENTE"
        print(f"  {nome}: banco {numeros[nome]} | numeros.md {esperado} | {marca}")
    print(f"  saldos do estoque diferentes do estoque.csv: {saldos_diferentes}")


def realinhar_sequencias(db: Session, tabelas: list[str]):
    """Depois do ensaio: a sequência volta para logo depois do maior id, sem o buraco dos ids reservados."""
    for tabela in tabelas:
        for (coluna,) in db.execute(text(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = :t AND column_default LIKE 'nextval%'"), {"t": tabela}):
            db.execute(text(
                f"SELECT setval(pg_get_serial_sequence(:qual, :c), coalesce((SELECT max({coluna}) FROM public.{tabela}), 0) + 1, false)"
            ), {"qual": f"public.{tabela}", "c": coluna})
    db.commit()


TOCADAS = [t.nome for t in TABELAS] + ["categoria_produto", "origem_migracao", "saldo_pontos", "consumo_pontos"]


def main():
    parser = argparse.ArgumentParser(description="Carga dos dados tratados da Vulto no banco da Casa Lorenzi")
    parser.add_argument("--origem", type=Path, default=Path("tratado_vulto"))
    parser.add_argument("--confirmar", action="store_true", help="grava de verdade (sem ele, é ensaio com ROLLBACK)")
    args = parser.parse_args()

    from src.config.settings import DATABASE_URL_DIRECT
    if not DATABASE_URL_DIRECT:
        raise SystemExit("DATABASE_URL_DIRECT não definida no .env")
    engine = create_engine(make_url(DATABASE_URL_DIRECT).set(drivername="postgresql+psycopg"))
    emails = ler(args.origem, USUARIO)["email"].tolist()

    with Session(engine) as db:
        if ja_carregada(db):
            raise SystemExit("A Vulto já está no banco (há unidade da marca vulto): a carga não roda duas vezes.")
        db.rollback()

        auth, criados = None, []
        if args.confirmar:
            from src.utils.supabase_admin import SupabaseAdmin
            auth = SupabaseAdmin()
            logins, criados = logins_definitivos(db, auth, emails)
        print("Carga definitiva (COMMIT se os números baterem)" if args.confirmar else "Ensaio (ROLLBACK no fim)")

        gravou = False
        try:
            db.execute(text("SET LOCAL statement_timeout = 0"))
            # data sem fuso no tratado é horário de Brasília (etapa 1)
            db.execute(text("SET LOCAL TIME ZONE 'America/Sao_Paulo'"))
            if not args.confirmar:
                logins = logins_do_ensaio(db, emails)
            chaves = carregar(db, args.origem, logins)
            numeros = calcular_numeros(db)
            mostrar(numeros, conferir_estoque(db, args.origem, chaves))
            if args.confirmar and not diferencas(numeros):
                db.commit()
                gravou = True
            else:
                db.rollback()
        finally:
            if not gravou:
                db.rollback()
                for id_usuario in criados:
                    auth.apagar_login(id_usuario)
                realinhar_sequencias(db, TOCADAS)

    if args.confirmar:
        print("\nGravado." if gravou else "\nNada gravado: os números não bateram (contas do Auth criadas agora apagadas).")
    else:
        print("\nEnsaio desfeito: nada ficou gravado." if not diferencas(numeros)
              else "\nEnsaio desfeito. Há números diferentes: confira antes da carga definitiva.")


if __name__ == "__main__":
    main()
