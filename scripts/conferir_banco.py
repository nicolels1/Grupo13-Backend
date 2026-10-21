# Confere no banco de verdade as garantias do usuário restrito da API, do saldo do estoque, do saldo de
# pontos e do cancelamento automático da reserva vencida (ADRs 0002 e 0005). Tudo roda numa transação desfeita
# no final: nada fica gravado.
# Uso, na raiz do repositório com o venv ativo e a DATABASE_URL do usuário restrito no .env:
#   python -m scripts.conferir_banco
import secrets
import sys

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from src.repositories import estoque_repository, fidelidade_repository

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
    conferir_fidelidade(c, db, local, sufixo)
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
        INSERT INTO pedido (codigo_venda, id_cliente, id_unidade, canal, canal_venda, modalidade, status,
                            valor_total, reserva_expira_em)
        VALUES (:codigo, :cliente, :u, 'online', 'site', 'retirada', 'aguardando_pagamento', 20,
                now() - interval '1 minute')
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


# migration 3532b648a2ad: o saldo de pontos só muda pelo trigger, o resgate gasta primeiro o ponto mais
# antigo (FIFO, K3), a devolução volta para os mesmos lotes e a plataforma nunca deixa o saldo negativo
def conferir_fidelidade(c: Conferencia, db: Session, local: dict, sufixo: str) -> None:
    dono = {"c": db.scalar(text(
        "INSERT INTO cliente_sem_conta (email, origem) VALUES (:email, 'migracao_vulto') RETURNING id_cliente_sem_conta"
    ), {"email": f"conferencia-{sufixo}@vulto.example"})}

    def movimento(tipo: str, pontos: int, quando: str, original: int | None = None) -> int:
        return db.scalar(text("""
            INSERT INTO movimento_pontos (id_cliente_sem_conta, marca, tipo, pontos, origem, criado_em,
                                          id_movimento_original)
            VALUES (:c, 'vulto', :tipo, :pontos, 'migracao', :quando, :original) RETURNING id_movimento
        """), {**dono, "tipo": tipo, "pontos": pontos, "quando": quando, "original": original})

    def restantes() -> list[int]:
        return list(db.scalars(text(
            "SELECT restante FROM lote_pontos WHERE id_cliente_sem_conta = :c ORDER BY ganho_em"
        ), dono))

    movimento("saldo_inicial", 1000, "2025-01-01 00:00-03")
    movimento("credito", 3000, "2025-06-01 12:00-03")
    resgate = movimento("resgate", -1500, "2025-07-01 12:00-03")
    categoria = db.scalar(text("SELECT categoria FROM saldo_pontos WHERE id_cliente_sem_conta = :c"), dono)
    c.registrar("o resgate gasta primeiro o ponto mais antigo (FIFO)", restantes() == [0, 2500], str(restantes()))
    c.registrar("o crédito sobe a categoria na hora", categoria == "prata", f"categoria {categoria}")
    movimento("estorno", 1500, "2025-07-02 12:00-03", resgate)
    c.registrar("o estorno do resgate devolve para os mesmos lotes", restantes() == [1000, 3000], str(restantes()))

    deve_recusar(c, db, "o saldo de pontos não muda direto (só por movimento)",
                 "UPDATE saldo_pontos SET saldo = 99 WHERE id_cliente_sem_conta = :c", dono)
    deve_recusar(c, db, "os lotes de pontos não mudam direto", "UPDATE lote_pontos SET restante = 0", {})
    deve_recusar(c, db, "movimento de pontos não é editado",
                 "UPDATE movimento_pontos SET motivo = 'x' WHERE id_cliente_sem_conta = :c", dono)
    deve_recusar(c, db, "regra de fidelidade não é apagada", "DELETE FROM regra_fidelidade", {})
    deve_recusar(c, db, "regra de fidelidade nova não vale para hoje nem para trás",
                 "INSERT INTO regra_fidelidade (marca, valida_desde, valor_ponto, pontos_por_real, "
                 "multiplicador_online, bonus_ouro, limite_prata, limite_ouro, resgate_minimo) "
                 "VALUES ('lorenzi', (now() AT TIME ZONE 'America/Sao_Paulo')::date, "
                 "0.05, 1, 2, 0, 2000, 5000, 100)", {})
    deve_recusar(c, db, "a plataforma não deixa o saldo de pontos negativo",
                 "INSERT INTO movimento_pontos (id_cliente_sem_conta, marca, tipo, pontos, origem, motivo) "
                 "VALUES (:c, 'vulto', 'ajuste', -99999, 'plataforma', 'teste')", dono)
    deve_recusar(c, db, "a API não roda o job de vencimento", "SELECT vence_pontos()", {})
    conferir_resgate(c, db, local, dono, sufixo)
    conferir_pontos_do_pagamento(c, db, local, dono, sufixo)


# migration 380e5e8b5f0a: o resgate vale os pontos mais antigos primeiro, cada um pelo valor da época, e os pontos
# voltam para os mesmos lotes quando o pedido é cancelado
def conferir_resgate(c: Conferencia, db: Session, local: dict, dono: dict, sufixo: str) -> None:
    previsto = fidelidade_repository.valor_do_resgate(db, None, dono["c"], "vulto", 1500)
    id_pedido = db.scalar(text("""
        INSERT INTO pedido (codigo_venda, id_cliente_sem_conta, id_unidade, canal, canal_venda, status, valor_total)
        VALUES (:codigo, :c, :u, 'loja_fisica', 'loja', 'entregue', 300) RETURNING id_pedido
    """), {**dono, **local, "codigo": f"CFR{sufixo}".upper()})
    resgate = db.scalar(text(
        "INSERT INTO movimento_pontos (id_cliente_sem_conta, marca, tipo, pontos, origem, id_pedido) "
        "VALUES (:c, 'vulto', 'resgate', -1500, 'plataforma', :p) RETURNING id_movimento"
    ), {**dono, "p": id_pedido})
    gasto = fidelidade_repository.valor_gasto(db, resgate)
    # 1.000 pontos de jan/2025 a R$ 0,10 e 500 de jun/2025 a R$ 0,05
    c.registrar("o resgate vale os pontos mais antigos primeiro, pelo valor da época",
                previsto == gasto == 125, f"previsto {previsto}, gasto {gasto}")
    db.execute(text("UPDATE pedido SET status = 'cancelado', motivo_cancelamento = 'cliente' WHERE id_pedido = :p"),
               {"p": id_pedido})
    saldo = db.scalar(text("SELECT saldo FROM saldo_pontos WHERE id_cliente_sem_conta = :c AND marca = 'vulto'"), dono)
    c.registrar("o pedido cancelado devolve os pontos do resgate", saldo == 4000, f"saldo {saldo}")

    # o que o cliente vê: 1.000 pontos a R$ 0,10 e 3.000 a R$ 0,05; o extrato tem os 6 movimentos da Vulto
    [vulto] = fidelidade_repository.resumo(db, None, dono["c"])
    _, movimentos = fidelidade_repository.extrato(db, None, dono["c"], "vulto", 50, 0)
    c.registrar("o saldo mostra quanto os pontos valem pela época",
                (vulto["saldo"], vulto["valor_em_reais"]) == (4000, 250),
                f"{vulto['saldo']} pontos, R$ {vulto['valor_em_reais']}")
    c.registrar("o extrato traz todos os movimentos do cliente", movimentos == 6, f"{movimentos} movimentos")


# migration 62556fe492f4: o pedido ganha pontos quando os pagamentos aprovados cobrem o total, e o estorno de
# dinheiro tira a parte proporcional; o pagamento migrado da Vulto não mexe nos pontos
def conferir_pontos_do_pagamento(c: Conferencia, db: Session, local: dict, dono: dict, sufixo: str) -> None:
    def pedido(codigo: str, canal: str, total: int, frete: int = 0) -> int:
        return db.scalar(text("""
            INSERT INTO pedido (codigo_venda, id_cliente_sem_conta, id_unidade, canal, canal_venda, modalidade,
                                status, valor_frete, valor_total, pago_em)
            VALUES (:codigo, :c, :u, :canal, :canal_venda, :modalidade, 'entregue', :frete, :total, now())
            RETURNING id_pedido
        """), {**dono, **local, "codigo": f"{codigo}{sufixo}".upper(), "canal": canal, "total": total, "frete": frete,
               "canal_venda": "site" if canal == "online" else "loja",
               "modalidade": "entrega" if canal == "online" else None})

    def pagamento(id_pedido: int, valor: int, metodo: str = "pix", origem: str | None = None) -> int:
        return db.scalar(text(
            "INSERT INTO pagamento (id_pedido, tipo, metodo, valor, status, origem) "
            "VALUES (:p, 'pagamento', :metodo, :valor, 'aprovado', :origem) RETURNING id_pagamento"
        ), {"p": id_pedido, "valor": valor, "metodo": metodo, "origem": origem})

    def estorno(id_pedido: int, original: int, valor: int) -> None:
        db.execute(text(
            "INSERT INTO pagamento (id_pedido, tipo, id_pagamento_original, metodo, valor, status, origem) "
            "VALUES (:p, 'estorno', :original, 'pix', :valor, 'aprovado', 'cancelamento')"
        ), {"p": id_pedido, "original": original, "valor": valor})

    def pontos(id_pedido: int) -> int:
        return db.scalar(text("SELECT coalesce(sum(pontos), 0) FROM movimento_pontos WHERE id_pedido = :p"),
                         {"p": id_pedido})

    loja = pedido("CFL", "loja_fisica", 200)
    primeiro = pagamento(loja, 120)
    sem_credito = pontos(loja)
    pagamento(loja, 80)
    c.registrar("o pedido ganha pontos só quando os pagamentos cobrem o total",
                (sem_credito, pontos(loja)) == (0, 200), f"{sem_credito} e {pontos(loja)} pontos")
    estorno(loja, primeiro, 100)
    c.registrar("o estorno de metade do dinheiro tira metade dos pontos", pontos(loja) == 100, f"{pontos(loja)} pontos")
    estorno(loja, primeiro, 20)
    estorno(loja, primeiro, 80)
    c.registrar("devolvido o total, não sobra ponto do pedido", pontos(loja) == 0, f"{pontos(loja)} pontos")

    online = pedido("CFO", "online", 110, frete=10)
    pagamento(online, 110)
    c.registrar("o online ganha em dobro e o frete não conta", pontos(online) == 200, f"{pontos(online)} pontos")

    # o cliente já gastou parte dos pontos do pedido: a devolução do dinheiro não trava e o saldo não fica negativo
    gasto = pedido("CFG", "loja_fisica", 100)
    pago = pagamento(gasto, 100)
    db.execute(text("INSERT INTO movimento_pontos (id_cliente_sem_conta, marca, tipo, pontos, origem, motivo) "
                    "VALUES (:c, 'lorenzi', 'ajuste', -250, 'plataforma', 'teste')"), dono)
    erro = tentar_e_manter(db, "INSERT INTO pagamento (id_pedido, tipo, id_pagamento_original, metodo, valor, status, "
                               "origem) VALUES (:p, 'estorno', :o, 'pix', 100, 'aprovado', 'cancelamento')",
                           {"p": gasto, "o": pago})
    saldo = db.scalar(text(
        "SELECT saldo FROM saldo_pontos WHERE id_cliente_sem_conta = :c AND marca = 'lorenzi'"
    ), dono)
    c.registrar("o estorno dos pontos já gastos para no saldo, sem travar a devolução", erro is None and saldo == 0,
                str(erro) if erro else f"saldo {saldo}")

    conferir_compra_ligada(c, db, local, sufixo)

    migrado = pedido("CFM", "loja_fisica", 50)
    pagamento(migrado, 50, metodo="nao_informado", origem="migracao")
    c.registrar("o pagamento migrado da Vulto não credita pontos", pontos(migrado) == 0, f"{pontos(migrado)} pontos")


# migration 5806135c2561: a compra no caixa só com o CPF na nota ganha os pontos quando passa para a conta,
# pela data da compra, se ainda não teriam vencido
def conferir_compra_ligada(c: Conferencia, db: Session, local: dict, sufixo: str) -> None:
    id_cliente = db.scalar(text("SELECT id_usuario FROM usuario WHERE tipo_conta = 'cliente' LIMIT 1"))
    if id_cliente is None:
        c.registrar("a compra do caixa ganha os pontos quando passa para a conta", False, "nenhum cliente no banco")
        return

    def compra(codigo: str, dias_atras: int) -> int:
        id_pedido = db.scalar(text("""
            INSERT INTO pedido (codigo_venda, id_unidade, cpf_nota, canal, canal_venda, status, valor_total, pago_em)
            VALUES (:codigo, :u, '52998224725', 'loja_fisica', 'loja', 'entregue', 150,
                    now() - make_interval(days => :dias))
            RETURNING id_pedido
        """), {**local, "codigo": f"{codigo}{sufixo}".upper(), "dias": dias_atras})
        db.execute(text("INSERT INTO pagamento (id_pedido, tipo, metodo, valor, status) "
                        "VALUES (:p, 'pagamento', 'dinheiro', 150, 'aprovado')"), {"p": id_pedido})
        db.execute(text("UPDATE pedido SET id_cliente = :c WHERE id_pedido = :p"), {"c": id_cliente, "p": id_pedido})
        return id_pedido

    recente = compra("CFC", 10)
    credito = db.execute(text("""
        SELECT m.pontos, l.ganho_em = p.pago_em AS na_data_da_compra
        FROM movimento_pontos m JOIN lote_pontos l USING (id_movimento) JOIN pedido p USING (id_pedido)
        WHERE m.id_pedido = :p
    """), {"p": recente}).first()
    c.registrar("a compra do caixa ganha os pontos quando passa para a conta, pela data da compra",
                credito is not None and tuple(credito) == (150, True), str(credito))
    # a Casa Lorenzi só tem programa de fidelidade desde 01/10/2026
    antiga = compra("CFA", 400)
    pontos = db.scalar(text("SELECT count(*) FROM movimento_pontos WHERE id_pedido = :p"), {"p": antiga})
    c.registrar("compra de antes do programa da marca não ganha pontos", pontos == 0, f"{pontos} movimento(s)")


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
