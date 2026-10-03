# Casa Lorenzi — Backend

API da plataforma de estoque, vendas e atendimento da Casa Lorenzi, desenvolvida no Case Tech da Trainee Insper Jr 2026.2. O backend atende duas frentes: a plataforma interna (equipe da loja) e a plataforma do cliente. O frontend fica em um repositório separado.

## Arquitetura e stack

```
Frontend  ──HTTPS + token──▶  API FastAPI (este repositório)  ──▶  PostgreSQL no Supabase
    │                                │
    └── login no Supabase Auth       └── valida o token com a chave pública do Supabase
```

O Supabase fornece identidade (Auth), banco (PostgreSQL) e arquivos (Storage). As regras de negócio e as permissões ficam no backend: o frontend não acessa o banco diretamente.

| Tecnologia | Função | Situação |
|---|---|---|
| Python + FastAPI | API | em uso |
| Uvicorn | servidor local | em uso |
| Supabase Auth + PyJWT | autenticação | em uso |
| python-dotenv | variáveis de ambiente | em uso |
| SQLAlchemy + psycopg | ORM e driver do PostgreSQL | conexão configurada (`src/database/`), ainda sem models |
| Alembic | migrations | conectado ao banco e aos models, ainda sem migrations |
| PostgreSQL (Supabase) | banco de dados | conexão configurada, ainda sem tabelas |
| Supabase Storage | fotos e anexos | planejado |
| Render | deploy | planejado |

## Estrutura do backend

```
src/
├── app.py          # cria o app FastAPI e define as rotas
├── middlewares/    # auth.py: valida o token do Supabase
├── config/         # settings.py: lê as variáveis do .env
├── entities/       # schemas de entrada e saída
├── models/         # modelos SQLAlchemy (tabelas)
├── database/       # base.py: classe Base dos models; session.py: engine, sessão e get_db
├── repositories/   # consultas e escritas no banco
├── use_cases/      # regras de negócio
├── utils/          # funções auxiliares
└── alembic/        # configuração de migrations (versions/ guarda as migrations)
```

Hoje têm código `app.py`, `middlewares/auth.py`, `config/` e `database/`. As demais pastas estão vazias e foram criadas para as próximas camadas.

## Autenticação e banco

**Autenticação.** O login acontece no Supabase Auth, feito pelo frontend. Cada requisição protegida envia o header `Authorization: Bearer <access_token>`. O backend busca a chave pública do projeto em `SUPABASE_URL/auth/v1/.well-known/jwks.json` e valida a assinatura (algoritmo ES256, audience `authenticated`). Para proteger uma rota, use a dependência `get_current_user`, que devolve o id do usuário (`sub` do token):

```python
from fastapi import Depends
from src.middlewares.auth import get_current_user

@app.get("/exemplo")
def exemplo(user_id: str = Depends(get_current_user)):
    ...
```

**Banco.** O backend usa duas conexões com o PostgreSQL do Supabase:

- a API usa `DATABASE_URL` (Transaction pooler, porta 6543);
- o Alembic usa `DATABASE_URL_DIRECT` (Direct connection, porta 5432). O `alembic.ini` não guarda URL: o `src/alembic/env.py` lê do `.env`.

Para uma rota acessar o banco, use a dependência `get_db`, que abre uma sessão por requisição:

```python
from fastapi import Depends
from sqlalchemy.orm import Session
from src.database.session import get_db

@app.get("/exemplo")
def exemplo(db: Session = Depends(get_db)):
    ...
```

Ainda não existem models nem migrations. Cada model novo herda de `Base` (`src/database/base.py`) e precisa ser importado em `src/models/__init__.py` para o Alembic enxergá-lo.

O passo a passo para criar models e migrations, os comandos do Alembic e as regras do banco (RLS em todas as tabelas, FKs com `RESTRICT`, referência a `auth.users`) estão no [ALEMBIC_GUIDE.md](ALEMBIC_GUIDE.md).

## Configuração e execução

Os comandos rodam na raiz do repositório (a pasta que contém `src/`).

**Pré-requisito:** Python. A instalação das dependências e a execução da API foram confirmadas com o **Python 3.14.3** no Windows. Outras versões recentes do Python 3 podem funcionar, mas não foram testadas.

```powershell
# 1. Criar e ativar o ambiente virtual
python -m venv .venv
.\.venv\Scripts\Activate.ps1        # macOS/Linux: source .venv/bin/activate

# 2. Instalar as dependências
pip install -r requirements.txt

# 3. Criar o .env a partir do exemplo e preencher as variáveis
Copy-Item .env.example .env         # macOS/Linux: cp .env.example .env

# 4. Rodar a API
uvicorn src.app:app --reload
```

| Variável | Descrição |
|---|---|
| `SUPABASE_URL` | URL do projeto Supabase (Project Settings → API → Project URL), no formato `https://<project-ref>.supabase.co` |
| `DATABASE_URL` | conexão da API: botão **Connect** do Supabase → Transaction pooler (porta 6543) |
| `DATABASE_URL_DIRECT` | conexão das migrations: botão **Connect** → Direct connection (porta 5432) |

Nas duas URLs do banco, troque `[YOUR-PASSWORD]` pela senha do banco e codifique caracteres especiais (`@` vira `%40`, por exemplo). O `.env.example` mostra o formato completo.

O `.env` está no `.gitignore` e não deve ser commitado. Sem `SUPABASE_URL`, a API não sobe; sem `DATABASE_URL_DIRECT`, o Alembic não roda.

Com o servidor rodando:

- API: http://127.0.0.1:8000/
- Documentação interativa: http://127.0.0.1:8000/docs (Swagger) e http://127.0.0.1:8000/redoc

## Rotas atuais

| Método | Rota | Autenticação | Resposta |
|---|---|---|---|
| GET | `/` | não | `{"status": "ok"}` |
| GET | `/protegida` | sim | mensagem de confirmação + `user_id` (rota de teste da autenticação) |

```powershell
curl.exe http://127.0.0.1:8000/                     # {"status":"ok"}
curl.exe http://127.0.0.1:8000/protegida            # 401 {"detail":"Token ausente"}
curl.exe -H "Authorization: Bearer <access_token>" http://127.0.0.1:8000/protegida
```

## Estado atual

- **Implementado:** API base, validação de tokens do Supabase Auth e configuração da conexão com o PostgreSQL (API e Alembic).
- **Estrutura preparada:** pastas das camadas em `src/`.
- **Próximos passos:** models e migrations, e rotas de negócio.

Não há testes automatizados no momento.

## Problemas comuns

| Erro | Causa provável |
|---|---|
| `ModuleNotFoundError` (fastapi, jwt…) | ambiente virtual não ativado ou dependências não instaladas |
| `No module named 'src'` | Uvicorn executado fora da raiz do repositório |
| `Invalid JWKS URI scheme` ao iniciar | `.env` ausente ou `SUPABASE_URL` vazia |
| `DATABASE_URL não definida no .env` / `DATABASE_URL_DIRECT não definida no .env` | variável do banco ausente no `.env` |
| Alembic trava ou não conecta pela conexão direta | a conexão direta do Supabase usa IPv6; se sua rede não tiver, use o Session pooler (porta 5432) em `DATABASE_URL_DIRECT` |
| 401 `Token ausente` / `Token inválido: ...` | header `Authorization` não enviado, token expirado ou de outro projeto |
| `DLL load failed ... nome do arquivo ou a extensão é muito grande` | caminho da pasta longo demais para o Windows; clone o repositório (ou crie o venv) em um caminho mais curto |
