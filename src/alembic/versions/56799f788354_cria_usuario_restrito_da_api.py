"""cria usuario restrito da api

Revision ID: 56799f788354
Revises: 76f97bb5af49
Create Date: 2026-10-06

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '56799f788354'
down_revision: Union[str, Sequence[str], None] = '76f97bb5af49'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Usuário de banco da API (case, seção 2). Nasce sem poder entrar: a senha não vai para o git.
# Depois desta migration, no SQL Editor do Supabase:
#   ALTER ROLE api_casalorenzi WITH LOGIN PASSWORD '<senha forte>';
# e a DATABASE_URL da API passa a usar esse usuário (as migrations continuam com o dono).
PAPEL = "api_casalorenzi"
POLITICA = f"{PAPEL}_acesso"

# o que nunca muda (trigger bloqueia_alteracao): só ler e inserir
IMUTAVEIS = ["movimentacao_estoque", "historico_preco", "historico_chamado"]
# lista fixa do sistema (migration 9b52eb4fbf53): só ler
SO_LEITURA = ["permissao"]
# o saldo só muda por movimentação (ADR 0005): a API altera só as outras colunas
ESTOQUE = "estoque"
COLUNAS_ESTOQUE = "quantidade_reservada, estoque_minimo, minimo_alterado_por, minimo_alterado_em, atualizado_em"
# fora isso, nada é apagado (desativa-se). Exceções: endereço salvo, que o cliente apaga, e as
# duas tabelas de ligação da Gestão (tirar permissão de um modelo, remover exceção de uma pessoa)
APAGAVEIS = ["endereco_cliente", "modelo_permissao", "usuario_permissao_excecao"]
DEMAIS = [
    "avaliacao", "categoria_produto", "chamado", "denuncia_avaliacao", "endereco_entrega", "foto_avaliacao",
    "imagem_produto", "item_pedido", "item_transferencia", "mensagem", "modelo_acesso", "pagamento", "pedido",
    "produto", "transferencia", "unidade", "usuario", "voto_util",
]
TODAS = IMUTAVEIS + SO_LEITURA + [ESTOQUE] + APAGAVEIS + DEMAIS

# a Gestão de contas consulta o login no Supabase Auth; o dono não pode liberar o esquema auth
# para outro usuário, então duas funções do dono respondem só o necessário (e só para a API)
FUNCOES_AUTH = {
    "login_por_email(text)": """
        CREATE FUNCTION login_por_email(email_buscado text) RETURNS uuid
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
            SELECT id FROM auth.users WHERE lower(email) = lower(email_buscado)
        $$;
    """,
    "login_confirmado(uuid)": """
        CREATE FUNCTION login_confirmado(id_login uuid) RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
            SELECT coalesce((SELECT email_confirmed_at IS NOT NULL FROM auth.users WHERE id = id_login), false)
        $$;
    """,
}


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(f"""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{PAPEL}') THEN
                CREATE ROLE {PAPEL} NOLOGIN;
            END IF;
        END $$;
    """)
    op.execute(f"GRANT USAGE ON SCHEMA public TO {PAPEL};")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {PAPEL};")

    for tabela in IMUTAVEIS:
        op.execute(f"GRANT SELECT, INSERT ON {tabela} TO {PAPEL};")
    for tabela in SO_LEITURA:
        op.execute(f"GRANT SELECT ON {tabela} TO {PAPEL};")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ({COLUNAS_ESTOQUE}) ON {ESTOQUE} TO {PAPEL};")
    for tabela in APAGAVEIS + DEMAIS:
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {tabela} TO {PAPEL};")
    for tabela in APAGAVEIS:
        op.execute(f"GRANT DELETE ON {tabela} TO {PAPEL};")

    # RLS ligado em todas as tabelas e sem regra para os papéis públicos: esta regra libera só a API
    for tabela in TODAS:
        op.execute(f"CREATE POLICY {POLITICA} ON {tabela} FOR ALL TO {PAPEL} USING (true) WITH CHECK (true);")

    for assinatura, criar in FUNCOES_AUTH.items():
        op.execute(criar)
        op.execute(f"REVOKE ALL ON FUNCTION {assinatura} FROM PUBLIC, anon, authenticated;")
        op.execute(f"GRANT EXECUTE ON FUNCTION {assinatura} TO {PAPEL};")

    # aviso do Supabase: o trigger do saldo roda como dono e não deve ser chamado pela API automática
    # (o trigger continua funcionando: disparar um trigger não exige EXECUTE)
    op.execute("REVOKE EXECUTE ON FUNCTION aplica_movimentacao() FROM PUBLIC, anon, authenticated;")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("GRANT EXECUTE ON FUNCTION aplica_movimentacao() TO PUBLIC, anon, authenticated;")
    for assinatura in reversed(list(FUNCOES_AUTH)):
        op.execute(f"DROP FUNCTION {assinatura};")
    for tabela in reversed(TODAS):
        op.execute(f"DROP POLICY {POLITICA} ON {tabela};")
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {PAPEL};")
    op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {PAPEL};")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {PAPEL};")
    op.execute(f"DROP ROLE {PAPEL};")
