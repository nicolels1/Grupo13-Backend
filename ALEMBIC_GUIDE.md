# Guia do Alembic

O Alembic versiona as mudanças no schema do banco. Cada migration é um arquivo em `src/alembic/versions/` que descreve uma alteração (criar tabela, adicionar coluna...) e é aplicada na mesma ordem em qualquer ambiente. Nenhuma tabela deve ser criada ou alterada à mão no painel do Supabase.

## Como está configurado

| Peça | Arquivo | Função |
|---|---|---|
| Conexão | `.env` → `DATABASE_URL_DIRECT` | o Alembic usa a conexão direta do Supabase (a API usa o pooler, em `DATABASE_URL`) |
| Ponto de entrada | `alembic.ini` | aponta para `src/alembic/`; não guarda URL nem senha |
| Configuração | `src/alembic/env.py` | lê a URL do `.env`, compara o banco com os models e aplica as regras abaixo |
| Models | `src/models/` | classes que herdam de `Base` (`src/database/base.py`) |
| Migrations | `src/alembic/versions/` | os arquivos gerados, commitados junto com o código |

Regras que o `env.py` aplica sozinho:

- **RLS em todas as tabelas, liberado só para a API.** Toda tabela criada pelo `--autogenerate` já vem, na migration, com `ENABLE ROW LEVEL SECURITY`, a regra que libera o usuário restrito da API (`api_casalorenzi`) e as permissões padrão dele: ler, inserir e alterar. Apagar não entra. Se a tabela fugir do padrão, ajuste o `GRANT` à mão na migration: histórico que nunca muda fica só com ler e inserir, e tabela de ligação que precisa apagar ganha `DELETE`. A tabela interna `alembic_version` também recebe RLS a cada execução.
- **Tabelas do Supabase são ignoradas.** Tabelas dos schemas `auth` e `storage` (como `auth.users`) podem ser declaradas nos models para servir de alvo de FK, mas o Alembic nunca tenta criá-las nem apagá-las.
- **Nomes padronizados de restrições** (`pk_`, `fk_`, `uq_`, `ix_`, `ck_`), definidos em `src/database/base.py`.

## Comandos

Rodar na raiz do repositório, com o venv ativo:

```powershell
alembic current                                   # versão aplicada no banco (também testa a conexão)
alembic history                                   # lista as migrations existentes
alembic revision --autogenerate -m "cria tabela x" # gera uma migration a partir dos models
alembic upgrade head                              # aplica todas as migrations pendentes
alembic downgrade -1                              # desfaz a última migration
alembic heads                                     # mostra as "cabeças" (deve haver só uma)
```

## Passo a passo para mudar o banco

1. Atualize o `main` (`git pull`) e rode `alembic upgrade head` para seu banco ficar na versão mais recente.
2. Crie ou altere o model em `src/models/`.
3. Se for um arquivo novo, importe-o em `src/models/__init__.py`. Sem isso, o Alembic não enxerga o model.
4. Gere a migration: `alembic revision --autogenerate -m "descricao curta"`.
5. **Leia o arquivo gerado** em `src/alembic/versions/` antes de aplicar. O autogenerate não detecta tudo: renomeações viram "apaga e cria", e `CHECK` e valores padrão às vezes precisam ser escritos à mão. Triggers, funções, views, GRANTs e policies nunca aparecem (veja "O que o `--autogenerate` não gera").
6. Aplique: `alembic upgrade head`.
7. Commite a migration **no mesmo commit/PR** do código que depende dela.

## Regras do modelo de dados para os models

Definidas no modelo de dados do projeto:

- **FKs com `ON DELETE RESTRICT`:** use `ForeignKey("tabela.coluna", ondelete="RESTRICT")`.
- **Desativação em vez de exclusão** (exceto endereço salvo): use a coluna `ativo` ou `status`, não `DELETE`.
- **`USUARIO.id_usuario` = `auth.users.id`:** declare a tabela do Supabase só como referência, por exemplo:

```python
from sqlalchemy import Table, Column, Uuid
from src.database.base import Base

auth_users = Table("users", Base.metadata, Column("id", Uuid, primary_key=True), schema="auth")
```

  e use `ForeignKey("auth.users.id", ondelete="RESTRICT")` na coluna `id_usuario`.

## O que o `--autogenerate` não gera

O autogenerate só compara tabelas, colunas, índices e restrições dos models. Ele **não detecta** e, por isso, nunca cria, altera nem apaga:

- triggers;
- funções (`CREATE FUNCTION`);
- views;
- `GRANT` e `REVOKE` (permissões do usuário de banco do FastAPI), fora o padrão que o `env.py` acrescenta em tabela nova;
- policies de RLS (o `env.py` só liga o RLS, não cria regras de acesso).

Esses objetos são escritos à mão com `op.execute`, numa migration própria (`alembic revision -m "descricao"`, sem `--autogenerate`) ou acrescentados a uma gerada. Regras:

- **Todo `upgrade` tem `downgrade`.** O `downgrade` desfaz na ordem inversa: apaga o trigger antes da função, revoga o que foi concedido, apaga a policy.
- **Mudar uma função já aplicada é migration nova.** Use `CREATE OR REPLACE FUNCTION` no `upgrade` e, no `downgrade`, recrie a versão anterior (não apague a função).
- **Teste o caminho de volta:** `alembic upgrade head`, `alembic downgrade -1` e `alembic upgrade head` de novo, sem erro.

Exemplo, com o trigger que atualiza o saldo do estoque a cada movimentação:

```python
def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION aplica_movimentacao() RETURNS trigger AS $$
        BEGIN
            -- atualiza (ou cria) a linha de estoque com NEW.quantidade
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_aplica_movimentacao
        AFTER INSERT ON movimentacao_estoque
        FOR EACH ROW EXECUTE FUNCTION aplica_movimentacao();
    """)
    op.execute("REVOKE UPDATE (quantidade) ON estoque FROM api_casalorenzi;")


def downgrade() -> None:
    op.execute("GRANT UPDATE (quantidade) ON estoque TO api_casalorenzi;")
    op.execute("DROP TRIGGER trg_aplica_movimentacao ON movimentacao_estoque;")
    op.execute("DROP FUNCTION aplica_movimentacao();")
```

`api_casalorenzi` é o usuário de banco da API (migration `56799f788354`). As migrations rodam com o dono do banco (`DATABASE_URL_DIRECT`), nunca com ele.

## Migrations em branches paralelas

- **Duas cabeças.** Se duas branches gerarem migrations a partir da mesma versão, o Alembic fica com duas cabeças (`alembic heads` mostra duas) e o `upgrade head` falha. Para corrigir, depois de atualizar a branch com o `main`, edite o `down_revision` da sua migration para apontar para a migration mais recente do `main`, ou rode `alembic merge heads -m "merge"`.
- **Migration já aplicada não se edita.** Se ela já rodou no banco compartilhado, crie uma nova migration com a correção.

## Problemas comuns

| Erro | Causa provável |
|---|---|
| `DATABASE_URL_DIRECT não definida no .env` | variável ausente no `.env` |
| Comando trava ou não conecta | a conexão direta do Supabase usa IPv6; se sua rede não tiver, use a URL do Session pooler (porta 5432) em `DATABASE_URL_DIRECT` |
| Migration gerada vazia | model não importado em `src/models/__init__.py` |
| `Multiple head revisions are present` | duas migrations com o mesmo pai; veja "Migrations em branches paralelas" |
| `Can't locate revision identified by '...'` | o banco está numa migration que não existe na sua branch; atualize com o `main` |
