# Confere no banco de verdade as garantias do usuário restrito da API, do saldo do estoque e do
# cancelamento automático da reserva vencida (ADRs 0002 e 0005). Tudo roda numa transação desfeita
# no final: nada fica gravado.
# Uso, na raiz do repositório com o venv ativo e a DATABASE_URL do usuário restrito no .env:
#   python -m scripts.conferir_banco
import secrets
import sys

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from src.repositories import estoque_repository

PAPEL_DA_API = "api_casalorenzi"


class Conferencia:
    """Guarda o resultado de cada verificação e imprime ✅ ou ❌."""

    def __init__(self):
        self.falhas = 0

    def registrar(self, descricao: str, ok: bool, detalhe: str = "") -> bool:
        print(f"{'✅' if ok else '❌'} {descricao}{f' ({detalhe})' if detalhe and not ok else ''}")
        self.falhas += not ok
        return ok


# executa dentro de um savepoint e sempre desfaz: serve para testar o que o banco deve recusar
def tentar(db: Session, sql: str, parametros: dict | None = None) -> DBAPIError | None:
    savepoint = db.begin_nested()
    try:
        db.execute(text(sql), parametros or {})
        return None
    except DBAPIError as erro:
        return erro
    finally:
        savepoint.rollback()


def deve_recusar(conferencia: Conferencia, db: Session, descricao: str, sql: str, parametros: dict | None = None):
    erro = tentar(db, sql, parametros)
    conferencia.registrar(descricao, erro is not None, "o banco aceitou")


def conferir(db: Session) -> Conferencia:
    c = Conferencia()

    # 1. quem está conectado
    papel = db.scalar(text("SELECT current_user"))
    passa_rls = db.scalar(text("SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user"))
    c.registrar(f"a API conecta como {PAPEL_DA_API}", papel == PAPEL_DA_API, f"conectado como {papel}")
    c.registrar("o usuário da API não passa por cima do RLS", not passa_rls, "passa por cima do RLS")

    # toda tabela precisa liberar a API: uma tabela esquecida só aparece quando a rota quebra
    sem_regra = db.scalars(text("""
        SELECT c.relname FROM pg_class c
        WHERE c.relnamespace = 'public'::regnamespace AND c.relkind = 'r' AND c.relname <> 'alembic_version'
          AND NOT EXISTS (
              SELECT 1 FROM pg_policies p
              WHERE p.schemaname = 'public' AND p.tablename = c.relname AND p.policyname = :regra
          )
        ORDER BY 1
    """), {"regra": f"{PAPEL_DA_API}_acesso"}).all()
    c.registrar("toda tabela tem a regra de RLS que libera a API", not sem_regra, ", ".join(sem_regra))
    sem_leitura = db.scalars(text("""
        SELECT c.relname FROM pg_class c
        WHERE c.relnamespace = 'public'::regnamespace AND c.relkind = 'r' AND c.relname <> 'alembic_version'
          AND NOT has_table_privilege(:papel, c.oid, 'SELECT')
        ORDER BY 1
    """), {"papel": PAPEL_DA_API}).all()
    c.registrar("a API consegue ler toda tabela", not sem_leitura, ", ".join(sem_leitura))

    # 2. saldo x movimentações no banco como está (deve vir vazio)
    divergencias = estoque_repository.listar_divergencias(db)
    c.registrar("saldo bate com a soma das movimentações", not divergencias, f"{len(divergencias)} linha(s) divergente(s)")

    # 3. cenário de teste dentro da transação (desfeito no final)
    sufixo = secrets.token_hex(3)
    ids = db.execute(text("""
        WITH u AS (
            INSERT INTO unidade (nome, tipo, despacha_online, rua, numero, bairro, cidade, uf, cep)
            VALUES (:nome, 'loja', false, 'Rua Teste', '1', 'Centro', 'São Paulo', 'SP', '01000000')
            RETURNING id_unidade
        ), c AS (
            INSERT INTO categoria_produto (nome) VALUES (:nome) RETURNING id_categoria
        ), p AS (
            INSERT INTO produto (id_categoria, nome, descricao_tecnica, descricao_cliente)
            SELECT id_categoria, :nome, 'teste', 'teste' FROM c RETURNING id_produto
        ), v AS (
            INSERT INTO variante (id_produto, sku, cor, tamanho, preco)
            SELECT id_produto, :nome, 'Única', 'U', 10 FROM p RETURNING id_variante
        )
        SELECT (SELECT id_unidade FROM u), (SELECT id_variante FROM v)
    """), {"nome": f"CONFERENCIA-{sufixo}"}).one()
    local = {"u": ids[0], "v": ids[1]}

    entrada = tentar_e_manter(db, """
        INSERT INTO movimentacao_estoque (id_variante, id_unidade, canal, tipo, quantidade)
        VALUES (:v, :u, 'loja_fisica', 'recebimento', 5)
    """, local)
    saldo = db.scalar(text(
        "SELECT quantidade FROM estoque WHERE id_variante = :v AND id_unidade = :u AND canal = 'loja_fisica'"
    ), local)
    c.registrar("a movimentação atualiza o saldo pelo trigger", entrada is None and saldo == 5,
                str(entrada) if entrada else f"saldo {saldo}, esperado 5")

    # 4. o que o banco deve recusar
    deve_recusar(c, db, "o saldo não muda direto (só por movimentação)",
                 "UPDATE estoque SET quantidade = 99 WHERE id_variante = :v AND id_unidade = :u", local)
    deve_recusar(c, db, "saída maior que o saldo é recusada",
                 "INSERT INTO movimentacao_estoque (id_variante, id_unidade, canal, tipo, quantidade, motivo) "
                 "VALUES (:v, :u, 'loja_fisica', 'ajuste', -10, 'teste')", local)
    deve_recusar(c, db, "movimentação não é editada",
                 "UPDATE movimentacao_estoque SET motivo = 'x' WHERE id_variante = :v", local)
    deve_recusar(c, db, "movimentação não é apagada", "DELETE FROM movimentacao_estoque WHERE id_variante = :v", local)
    # unidade sem nada ligado: assim a recusa vem da permissão, e não da chave estrangeira
    vazia = db.scalar(text("""
        INSERT INTO unidade (nome, tipo, despacha_online, rua, numero, bairro, cidade, uf, cep)
        VALUES (:nome, 'loja', false, 'Rua Teste', '2', 'Centro', 'São Paulo', 'SP', '01000000')
        RETURNING id_unidade
    """), {"nome": f"CONFERENCIA-VAZIA-{sufixo}"})
    deve_recusar(c, db, "unidade não é apagada (só desativada)", "DELETE FROM unidade WHERE id_unidade = :u",
                 {"u": vazia})
    deve_recusar(c, db, "a API não lê o auth.users direto", "SELECT count(*) FROM auth.users")

    deve_recusar(c, db, "pedido não é apagado", "DELETE FROM pedido WHERE id_unidade = :u", local)
    deve_recusar(c, db, "troca ou devolução sem pedido é recusada (ADR 0015)",
                 "INSERT INTO movimentacao_estoque (id_variante, id_unidade, canal, tipo, quantidade) "
                 "VALUES (:v, :u, 'loja_fisica', 'devolucao', 1)", local)

    # 5. o que a API precisa conseguir
    login = tentar(db, "SELECT login_por_email(:email)", {"email": f"ninguem-{sufixo}@casalorenzi.example"})
    c.registrar("a API consulta login pela função do banco", login is None, str(login))
    conferir_reserva_vencida(c, db, local, sufixo)
    divergencias = estoque_repository.listar_divergencias(db)
    c.registrar("depois do teste, o saldo continua batendo", not divergencias, f"{len(divergencias)} divergente(s)")
    return c


# migration 03aeb347f6cf: a função que o pg_cron roda a cada minuto cancela o pedido com a reserva
# vencida e devolve as peças ao disponível (ADR 0002)
def conferir_reserva_vencida(c: Conferencia, db: Session, local: dict, sufixo: str) -> None:
    id_cliente = db.scalar(text("SELECT id_usuario FROM usuario WHERE tipo_conta = 'cliente' LIMIT 1"))
    if id_cliente is None:
        c.registrar("reserva vencida é cancelada pela função do pg_cron", False, "nenhum cliente no banco para o teste")
        return
    db.execute(text("""
        INSERT INTO movimentacao_estoque (id_variante, id_unidade, canal, tipo, quantidade)
        VALUES (:v, :u, 'online', 'recebimento', 3)
    """), local)
    db.execute(text(
        "UPDATE estoque SET quantidade_reservada = 2 WHERE id_variante = :v AND id_unidade = :u AND canal = 'online'"
    ), local)
    id_pedido = db.scalar(text("""
        INSERT INTO pedido (codigo_venda, id_cliente, id_unidade, canal, modalidade, status, valor_total,
                            reserva_expira_em)
        VALUES (:codigo, :cliente, :u, 'online', 'retirada', 'aguardando_pagamento', 20, now() - interval '1 minute')
        RETURNING id_pedido
    """), {**local, "codigo": f"CF{sufixo}".upper(), "cliente": id_cliente})
    db.execute(text("INSERT INTO item_pedido (id_pedido, id_variante, quantidade, preco_unitario) "
                    "VALUES (:p, :v, 2, 10)"), {**local, "p": id_pedido})

    erro = tentar_e_manter(db, "SELECT cancela_vencidos()", {})
    status, motivo = db.execute(text("SELECT status, motivo_cancelamento FROM pedido WHERE id_pedido = :p"),
                                {"p": id_pedido}).one()
    reservada = db.scalar(text(
        "SELECT quantidade_reservada FROM estoque WHERE id_variante = :v AND id_unidade = :u AND canal = 'online'"
    ), local)
    c.registrar("reserva vencida é cancelada pela função do pg_cron",
                erro is None and (status, motivo, reservada) == ("cancelado", "reserva_vencida", 0),
                str(erro) if erro else f"pedido {status}/{motivo}, reservado {reservada}")


# executa dentro de um savepoint e mantém o resultado (desfeito junto com a transação no final)
def tentar_e_manter(db: Session, sql: str, parametros: dict) -> DBAPIError | None:
    savepoint = db.begin_nested()
    try:
        db.execute(text(sql), parametros)
        savepoint.commit()
        return None
    except DBAPIError as erro:
        savepoint.rollback()
        return erro


def main() -> None:
    from src.database.session import SessionLocal

    with SessionLocal() as db:
        try:
            conferencia = conferir(db)
        finally:
            db.rollback()  # nada do teste fica no banco
    print("\nTudo certo." if conferencia.falhas == 0 else f"\n{conferencia.falhas} verificação(ões) falharam.")
    sys.exit(1 if conferencia.falhas else 0)


if __name__ == "__main__":
    main()
