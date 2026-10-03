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
| SQLAlchemy | ORM | dependência no `requirements.txt`, ainda não usada no código |
| Alembic | migrations | estrutura inicial criada (`alembic.ini`, `src/alembic/`), sem conexão com o banco e sem migrations |
| PostgreSQL (Supabase) | banco de dados | planejado |
| Supabase Storage | fotos e anexos | planejado |
| Render | deploy | planejado |

## Estrutura do backend

```
src/
├── app.py          # cria o app FastAPI e define as rotas
├── middlewares/    # auth.py: valida o token do Supabase
├── config/         # configurações e variáveis de ambiente
├── entities/       # schemas de entrada e saída
├── models/         # modelos SQLAlchemy (tabelas)
├── database/       # conexão e sessão com o PostgreSQL
├── repositories/   # consultas e escritas no banco
├── use_cases/      # regras de negócio
├── utils/          # funções auxiliares
└── alembic/        # configuração de migrations
```

Hoje só `app.py` e `middlewares/auth.py` têm código. As demais pastas estão vazias e foram criadas para as próximas camadas.

## Autenticação e banco

**Autenticação.** O login acontece no Supabase Auth, feito pelo frontend. Cada requisição protegida envia o header `Authorization: Bearer <access_token>`. O backend busca a chave pública do projeto em `SUPABASE_URL/auth/v1/.well-known/jwks.json` e valida a assinatura (algoritmo ES256, audience `authenticated`). Para proteger uma rota, use a dependência `get_current_user`, que devolve o id do usuário (`sub` do token):

```python
from fastapi import Depends
from src.middlewares.auth import get_current_user

@app.get("/exemplo")
def exemplo(user_id: str = Depends(get_current_user)):
    ...
```

**Banco.** O backend ainda não se conecta ao PostgreSQL. Ainda não existem modelos, sessão ou migrations; o `alembic.ini` mantém a URL de exemplo gerada pelo `alembic init`.

## Configuração e execução

Os comandos rodam na raiz do repositório (a pasta que contém `src/`).

**Pré-requisito:** Python. A instalação das dependências e a execução da API foram confirmadas com o **Python 3.14.3** no Windows. Outras versões recentes do Python 3 podem funcionar, mas não foram testadas.

```powershell
# 1. Criar e ativar o ambiente virtual
python -m venv .venv
.\.venv\Scripts\Activate.ps1        # macOS/Linux: source .venv/bin/activate

# 2. Instalar as dependências
pip install -r requirements.txt

# 3. Criar o .env a partir do exemplo e preencher SUPABASE_URL
Copy-Item .env.example .env         # macOS/Linux: cp .env.example .env

# 4. Rodar a API
uvicorn src.app:app --reload
```

| Variável | Descrição |
|---|---|
| `SUPABASE_URL` | URL do projeto Supabase (Project Settings → API → Project URL), no formato `https://<project-ref>.supabase.co` |

O `.env` está no `.gitignore` e não deve ser commitado. Sem `SUPABASE_URL`, a API não sobe.

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

- **Implementado:** API base e validação de tokens do Supabase Auth.
- **Estrutura preparada:** pastas das camadas em `src/` e configuração inicial do Alembic.
- **Próximos passos:** conexão com o PostgreSQL, modelos e migrations, e rotas de negócio.

Não há testes automatizados no momento.

## Problemas comuns

| Erro | Causa provável |
|---|---|
| `ModuleNotFoundError` (fastapi, jwt…) | ambiente virtual não ativado ou dependências não instaladas |
| `No module named 'src'` | Uvicorn executado fora da raiz do repositório |
| `Invalid JWKS URI scheme` ao iniciar | `.env` ausente ou `SUPABASE_URL` vazia |
| 401 `Token ausente` / `Token inválido: ...` | header `Authorization` não enviado, token expirado ou de outro projeto |
| `DLL load failed ... nome do arquivo ou a extensão é muito grande` | caminho da pasta longo demais para o Windows; clone o repositório (ou crie o venv) em um caminho mais curto |
