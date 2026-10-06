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
| Render | deploy | em uso: https://grupo13-backend-megw.onrender.com |

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
| `CORS_ORIGINS` | sites que podem chamar a API pelo navegador, separados por vírgula. Vazio: só `http://localhost:5173` (Vite) |
| `CORS_ORIGIN_REGEX` | opcional: expressão regular para liberar vários endereços, como os previews da Vercel |

Nas duas URLs do banco, troque `[YOUR-PASSWORD]` pela senha do banco e codifique caracteres especiais (`@` vira `%40`, por exemplo). O `.env.example` mostra o formato completo.

O `.env` está no `.gitignore` e não deve ser commitado. Sem `SUPABASE_URL` ou `DATABASE_URL`, a API não sobe; sem `DATABASE_URL_DIRECT`, o Alembic não roda.

Com o servidor rodando:

- Documentação interativa: http://127.0.0.1:8000/docs (Swagger) e http://127.0.0.1:8000/redoc
- Saúde da API e do banco: http://127.0.0.1:8000/health

## Rotas atuais

As rotas de negócio são o contrato da API: por enquanto todas respondem `{"items": []}`, sem consultar o banco. A lista completa, com parâmetros, fica em `/docs`.

| Método | Rota | Login | Resposta atual |
|---|---|---|---|
| GET | `/health` | não | `{"status": "ok"}` depois de consultar o banco; 503 se o banco não responder |
| GET | `/categorias`, `/produtos`, `/produtos/{id}/imagens`, `/unidades`, `/estoque`, `/avaliacoes` | não | `{"items": []}` |
| GET | `/movimentacoes-estoque`, `/transferencias`, `/pedidos`, `/enderecos`, `/pagamentos`, `/chamados`, `/chamados/{id}/mensagens`, `/usuarios` | sim | `{"items": []}` |

Rotas com login respondem 401 sem token ou com token inválido, e 503 se o Supabase não responder.

```powershell
curl.exe http://127.0.0.1:8000/health                # {"status":"ok"}
curl.exe http://127.0.0.1:8000/pedidos               # 401 {"detail":"Token ausente"}
curl.exe -H "Authorization: Bearer <access_token>" http://127.0.0.1:8000/pedidos
```

## Deploy (Render)

A API está publicada em **https://grupo13-backend-megw.onrender.com** (documentação em `/docs`). O Render publica de novo a cada merge na `main`.

| Configuração | Valor |
|---|---|
| Build command | `pip install -r requirements.txt` |
| Start command | `uvicorn src.app:app --host 0.0.0.0 --port $PORT` |
| Variáveis de ambiente | `SUPABASE_URL`, `DATABASE_URL`, `CORS_ORIGINS` (e `CORS_ORIGIN_REGEX`, se usar previews), `PYTHON_VERSION=3.14.3` |
| Health check path | `/docs` (não use `/health` aqui: o Render chama o health check com frequência e manteria conexões abertas no banco) |

`DATABASE_URL_DIRECT` não vai para o Render: as migrations rodam a partir da máquina de quem desenvolve.

No plano gratuito, o Render desliga a API depois de 15 minutos sem requisições, e a primeira chamada seguinte leva cerca de 1 minuto. Para evitar isso, um serviço externo de monitoramento (como o UptimeRobot) deve chamar `/health` a cada 10 minutos; como a rota consulta o banco, isso também mantém o Supabase ativo.

## Integração com o frontend

- **URL base:** `https://grupo13-backend-megw.onrender.com` em produção e `http://127.0.0.1:8000` localmente. No frontend (Vite), guarde-a numa variável de ambiente, por exemplo `VITE_API_URL`.
- **Login:** o frontend faz login no Supabase Auth e envia o token em toda rota protegida, no header `Authorization: Bearer <access_token>`. A API não usa cookies.
- **CORS:** o navegador só deixa o frontend chamar a API se o endereço dele estiver em `CORS_ORIGINS`. Ao publicar o frontend na Vercel, adicione o endereço dele (sem barra no final) a `CORS_ORIGINS` no Render, por exemplo `https://<projeto>.vercel.app,http://localhost:5173`. Para liberar também os previews da Vercel, use `CORS_ORIGIN_REGEX`, por exemplo `https://<projeto>-.*\.vercel\.app`.
- **Erros:** as respostas de erro vêm no formato `{"detail": "..."}`. 401 = sem login válido; 503 = banco ou Supabase indisponível.

## Testes

Os testes ficam em `tests/` e usam o pytest (já incluído no `requirements.txt`). Na raiz do repositório, com o venv ativo:

```powershell
pytest            # roda todos os testes
pytest -v         # mostra o resultado de cada teste
```

Os testes atuais não precisam de banco nem de internet: a chave do Supabase, o banco e o Supabase Auth são substituídos por versões falsas criadas no próprio teste.

| Arquivo | O que cobre |
|---|---|
| `tests/test_auth.py` | `get_current_user`: sem token e sem `Bearer` (401), token válido (200 com o id do usuário), token malformado, expirado, com audience errada, assinado por outra chave ou por chave que o Supabase não publica (401), Supabase fora do ar (503) |
| `tests/test_database.py` | `session.py`: erro claro sem `DATABASE_URL`, uso do driver psycopg e fechamento da sessão do `get_db` |
| `tests/test_cors.py` | CORS: site liberado recebe permissão, site desconhecido é recusado, previews pela expressão regular |
| `tests/test_health.py` | `/health`: 200 com o banco respondendo, 503 com o banco fora |
| `tests/test_criar_admin.py` | `criar_admin`: cria login e usuário no modelo Admin; recusa sem modelo Admin, com e-mail já usado ou login novo sem senha; reaproveita e confirma login existente; apaga o login criado se a gravação falhar, mas nunca um login que já existia |
| `tests/test_permissoes.py` | permissão efetiva (modelo de acesso, exceções `acrescentar`/`retirar`, Admin, permissões de gestão só para Admin) e as dependências das rotas: conta não cadastrada ou não ativa (403), id do token inválido (401), sem permissão (403), cliente sem permissão interna |

Rode os testes antes de cada commit. Toda função nova deve ganhar um teste.

### Testes no VS Code (sem terminal)

O repositório já traz a configuração em `.vscode/`. Com o venv criado e as dependências instaladas:

1. Instale a extensão **Python** (o VS Code sugere ao abrir o projeto).
2. Escolha o Python do venv: `Ctrl+Shift+P` → **Python: Select Interpreter** → `.venv`.
3. Abra o painel **Testing** (ícone de frasco na barra lateral) e clique em ▶ para rodar todos os testes, ou no ▶ ao lado de um arquivo ou teste para rodar só ele.

Cada teste aparece com ✅ ou ❌; clicando num teste que falhou, o VS Code mostra o erro na linha do código.

### Testes no GitHub (CI)

O GitHub Actions roda o pytest sozinho a cada push na `main` e em todo Pull Request para a `main` (configuração em `.github/workflows/testes.yml`). Não precisa de terminal nem de `.env`:

- **No Pull Request:** o resultado aparece no fim da página do PR. Verde = todos os testes passaram; vermelho = algum falhou e o PR não deve ser mergeado antes da correção.
- **Na aba Actions** do repositório: histórico de todas as execuções. Clique em uma execução e depois em `pytest` para ver o resultado de cada teste.
- **Rodar quando quiser:** aba Actions → **Testes** → **Run workflow**.

O CI usa o mesmo Python do Render (3.14.3). Se a versão mudar no Render, atualize também o `testes.yml`.

## Estado atual

- **Implementado:** contrato das rotas (ainda sem dados), validação de tokens do Supabase Auth, conexão com o PostgreSQL (API e Alembic), CORS para o frontend, rota `/health`, deploy no Render e testes automatizados com pytest.
- **Estrutura preparada:** pastas das camadas em `src/`.
- **Próximos passos:** models e migrations, e rotas de negócio.

## Problemas comuns

| Erro | Causa provável |
|---|---|
| `ModuleNotFoundError` (fastapi, jwt…) | ambiente virtual não ativado ou dependências não instaladas |
| `No module named 'src'` | Uvicorn executado fora da raiz do repositório |
| `Invalid JWKS URI scheme` ao iniciar | `.env` ausente ou `SUPABASE_URL` vazia |
| `DATABASE_URL não definida no .env` / `DATABASE_URL_DIRECT não definida no .env` | variável do banco ausente no `.env` |
| No navegador: `blocked by CORS policy` / `No 'Access-Control-Allow-Origin' header` | o endereço do frontend não está em `CORS_ORIGINS` (confira se não sobrou `/` no final) |
| `DLL load failed while importing pq: Uma política de Controle de Aplicativo bloqueou este arquivo` | o Smart App Control do Windows bloqueou o driver psycopg; a API, o Alembic e os testes que usam o banco não rodam nessa máquina enquanto o bloqueio existir; os testes continuam rodando no GitHub (CI) |
| Alembic trava ou não conecta pela conexão direta | a conexão direta do Supabase usa IPv6; se sua rede não tiver, use o Session pooler (porta 5432) em `DATABASE_URL_DIRECT` |
| 401 `Token ausente` / `Token inválido: ...` | header `Authorization` não enviado, token expirado ou de outro projeto |
| `DLL load failed ... nome do arquivo ou a extensão é muito grande` | caminho da pasta longo demais para o Windows; clone o repositório (ou crie o venv) em um caminho mais curto |
