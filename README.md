# Casa Lorenzi — Backend

API da plataforma de estoque, catálogo e atendimento da Casa Lorenzi, desenvolvida no Case Tech da Trainee Insper Jr 2026.2. Atende duas frentes com entrada única: a **plataforma interna** (equipe da loja) e a **plataforma do cliente**. O frontend fica no repositório [Grupo13-Frontend](https://github.com/nicolels1/Grupo13-Frontend).

- **API publicada:** https://grupo13-backend-megw.onrender.com
- **Documentação interativa:** https://grupo13-backend-megw.onrender.com/docs (Swagger) e `/redoc`

> No plano gratuito do Render, a API dorme depois de 15 minutos sem uso. A primeira chamada depois disso leva cerca de 1 minuto; as seguintes respondem normalmente.

## Sumário

- [Arquitetura e stack](#arquitetura-e-stack)
- [Estrutura do backend](#estrutura-do-backend)
- [Autenticação e permissões](#autenticação-e-permissões)
- [Banco de dados](#banco-de-dados)
- [Como rodar localmente](#como-rodar-localmente)
- [Scripts](#scripts)
- [Rotas](#rotas)
- [Convenções da API](#convenções-da-api)
- [Testes](#testes)
- [Deploy (Render)](#deploy-render)
- [Integração com o frontend](#integração-com-o-frontend)
- [Estado atual](#estado-atual)
- [Documentação do projeto](#documentação-do-projeto)
- [Como contribuir](#como-contribuir)
- [Problemas comuns](#problemas-comuns)

## Arquitetura e stack

```
Frontend (Vercel) ──HTTPS + token──▶ API FastAPI (Render, este repositório) ──▶ PostgreSQL (Supabase)
       │                                   │
       └── login no Supabase Auth          ├── valida o token com a chave pública do Supabase
                                           └── cria logins no Supabase Auth (cadastro e Gestão)
```

O Supabase fornece a identidade (Auth) e o banco (PostgreSQL). Todo acesso a dados passa pelo FastAPI: o frontend nunca fala direto com o banco, e as tabelas ficam fechadas para a API automática do Supabase.

| Tecnologia | Função |
|---|---|
| Python 3.14 + FastAPI | API |
| Uvicorn | servidor |
| Pydantic | validação de entrada e saída |
| SQLAlchemy + psycopg | ORM e driver do PostgreSQL |
| Alembic | migrations |
| PostgreSQL (Supabase) | banco de dados, com triggers e RLS |
| Supabase Auth + PyJWT | login e validação do token |
| httpx2 | chamadas à API de administração do Supabase Auth |
| pytest + GitHub Actions | testes automatizados e CI |
| Render | deploy |

## Estrutura do backend

```
src/
├── app.py          # cria o app, registra middlewares e rotas
├── config/         # settings.py (variáveis do .env) e logs.py (logs com id da requisição)
├── database/       # base.py (Base dos models) e session.py (engine, sessão e get_db)
├── entities/       # schemas Pydantic de entrada e saída de cada área
├── models/         # tabelas SQLAlchemy (27 tabelas do modelo de dados)
├── repositories/   # única camada que consulta e grava no banco
├── use_cases/      # regras de negócio
├── routes/         # rotas FastAPI, uma por área
├── middlewares/    # auth.py (token), permissoes.py (conta ativa e permissões), cors.py, erros.py, requisicao.py
├── utils/          # cpf.py e supabase_admin.py (cliente do Supabase Auth)
└── alembic/        # env.py e versions/ (migrations)
scripts/            # criar_admin, carregar_demo e conferir_banco
tests/              # testes do pytest
docs/               # case, ADRs
```

Caminho de uma requisição:

```
rota (routes/) → token (middlewares/auth.py) → conta ativa e permissão (middlewares/permissoes.py)
   → regra de negócio (use_cases/) → banco (repositories/ → models/ → PostgreSQL)
```

## Autenticação e permissões

**Autenticação (quem é).** O login acontece no Supabase Auth. Cada rota protegida recebe o header `Authorization: Bearer <access_token>`; o backend valida a assinatura com a chave pública do projeto (`SUPABASE_URL/auth/v1/.well-known/jwks.json`, algoritmo ES256, audience `authenticated`). O cliente também pode entrar com **CPF e senha** pela rota `POST /login/cpf`: o backend troca o CPF pela sessão do Supabase sem expor o e-mail.

**Autorização (o que pode).** Tipo de conta e permissões ficam no banco, nunca no token (ADR 0001), e são lidos a cada requisição: mudanças valem na hora.

- Toda conta tem tipo `interna` ou `cliente` e status `ativa`, `pendente_ativacao` ou `inativa`. Só conta ativa usa a API.
- A conta interna está ligada a um **modelo de acesso** (Admin, Funcionário, Estoquista, Atendente…) e pode ter **exceções** que acrescentam ou retiram permissões.
- O Admin tem todas as permissões. As permissões da Gestão (`gerenciar_contas`, `gerenciar_modelos_acesso`, `gerenciar_unidades`) são só dele, garantido por trigger (ADRs 0009 e 0010).
- São 15 códigos de permissão; a lista e a tabela por modelo estão na seção 6 do [case](docs/case/Casa_Lorenzi_Case_Completo.md).

Dependências prontas para proteger uma rota (`src/middlewares/permissoes.py`):

```python
from src.middlewares.permissoes import exige_permissao, exige_cliente, get_usuario_ativo

usuario: Usuario = Depends(exige_permissao("movimentar_estoque"))  # conta interna com a permissão
cliente: Usuario = Depends(exige_cliente)                          # conta de cliente ativa
usuario: Usuario = Depends(get_usuario_ativo)                      # qualquer conta ativa
```

Respostas: **401** sem token ou com token inválido; **403** conta não cadastrada, não ativa ou sem permissão; **503** se o Supabase não responder.

**Cadastro.** Toda conta é criada pelo backend, que cria o login no Supabase Auth e a linha em `usuario` juntos (ADR 0008); o cadastro aberto no Auth fica desligado. O cliente se cadastra por `POST /clientes`; contas internas são criadas pelo Admin na Gestão, por convite ou com senha provisória.

## Banco de dados

O backend usa duas conexões com o PostgreSQL do Supabase:

| Variável | Usuário | Uso |
|---|---|---|
| `DATABASE_URL` | `api_casalorenzi` (restrito), Transaction pooler, porta 6543 | a API e os scripts |
| `DATABASE_URL_DIRECT` | dono do banco, Direct connection, porta 5432 | só as migrations do Alembic |

**Duas camadas de proteção**, que valem para qualquer caminho, inclusive o painel do Supabase:

- **Usuário restrito da API** (`api_casalorenzi`, migration `56799f788354`): não apaga dados (exceto as ligações da Gestão), não edita movimentações nem históricos, não altera o saldo do estoque direto e não lê o `auth.users`. Todas as tabelas têm RLS ligado, com uma regra que libera só ele.
- **Triggers:** o saldo de `estoque` é atualizado pelo banco a cada movimentação e nunca fica negativo (ADR 0005); movimentações e históricos não são editados; as regras do Admin, do CD, do estorno e do item do chamado são garantidas no banco.

O histórico de estoque é calculado somando as movimentações até a data pedida, sem snapshot (ADR 0006). A rota `GET /estoque/divergencias` lista as linhas em que o saldo não bate com as movimentações e deve vir sempre vazia.

O modelo de dados completo (27 tabelas e valores aceitos) está na seção 7 do [case](docs/case/Casa_Lorenzi_Case_Completo.md). Comandos do Alembic, passo a passo para mudar o banco e as regras das migrations estão no [ALEMBIC_GUIDE.md](ALEMBIC_GUIDE.md).

## Como rodar localmente

Os comandos rodam na raiz do repositório (a pasta que contém `src/`).

**Pré-requisito:** Python 3.14 (testado com o 3.14.3, a mesma versão do Render e do CI).

```powershell
# 1. Criar e ativar o ambiente virtual
python -m venv .venv
.\.venv\Scripts\Activate.ps1        # macOS/Linux: source .venv/bin/activate

# 2. Instalar as dependências
pip install -r requirements.txt

# 3. Criar o .env a partir do exemplo e preencher as variáveis
Copy-Item .env.example .env         # macOS/Linux: cp .env.example .env

# 4. Deixar o banco na versão mais recente (só se houver migration nova)
alembic upgrade head

# 5. Rodar a API
uvicorn src.app:app --reload
```

Com o servidor rodando: http://127.0.0.1:8000/docs (Swagger) e http://127.0.0.1:8000/health.

### Variáveis de ambiente

| Variável | Obrigatória | Descrição |
|---|---|---|
| `SUPABASE_URL` | sim | URL do projeto (Project Settings → API), no formato `https://<project-ref>.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | sim | chave de serviço (Project Settings → API Keys → secret). Cria logins no cadastro e na Gestão. Dá acesso total ao projeto: só no backend, nunca no frontend nem no git |
| `SUPABASE_PUBLISHABLE_KEY` | sim | chave pública (Publishable key), usada no login por CPF |
| `DATABASE_URL` | sim | conexão da API com o usuário `api_casalorenzi`: botão **Connect** → Transaction pooler (porta 6543) |
| `DATABASE_URL_DIRECT` | só para migrations | conexão do dono do banco: **Connect** → Direct connection (porta 5432) |
| `CORS_ORIGINS` | não | frontends liberados, separados por vírgula e sem `/` no final. Vazio: só `http://localhost:5173` |
| `CORS_ORIGIN_REGEX` | não | expressão regular para liberar vários endereços, como os previews da Vercel |
| `DEMO_SENHA` | não | senha das contas criadas por `carregar_demo`; sem ela, o script pede na hora |

Nas URLs do banco, codifique caracteres especiais da senha (`@` vira `%40`). O `.env.example` mostra o formato completo. O `.env` está no `.gitignore` e nunca deve ser commitado; peça os valores a quem mantém o projeto Supabase.

## Scripts

Rodam na raiz do repositório, com o venv ativo e o `.env` preenchido:

| Comando | O que faz |
|---|---|
| `python -m scripts.criar_admin` | cria a primeira conta Admin (login no Supabase Auth + linha em `usuario`). Pede nome, e-mail e senha |
| `python -m scripts.carregar_demo` | carrega o cenário de demonstração: unidades, catálogo, modelos de acesso, contas internas e de cliente e 30 dias de movimentações de estoque. Pode rodar de novo sem duplicar nada |
| `python -m scripts.conferir_banco` | confere no banco de verdade as garantias do usuário restrito, do RLS e do saldo do estoque. Roda numa transação desfeita no final: nada fica gravado |

## Rotas

A lista completa, com parâmetros e formatos, está em `/docs`. Resumo por área:

### Saúde e contas

| Método | Rota | Acesso | Descrição |
|---|---|---|---|
| GET | `/health` | público | consulta o banco; 503 se ele não responder |
| POST | `/clientes` | público | cadastro de cliente (nome, e-mail, CPF e senha) |
| POST | `/login/cpf` | público | login do cliente por CPF e senha; devolve a sessão do Supabase |
| GET | `/me` | conta ativa | perfil, tipo de conta e permissões efetivas (monta a tela inicial) |

### Catálogo

| Método | Rota | Acesso | Descrição |
|---|---|---|---|
| GET | `/categorias` | público | categorias |
| POST, PATCH | `/categorias`, `/categorias/{id}` | `gerenciar_catalogo` | cria e altera categoria |
| GET | `/produtos`, `/produtos/{id}` | público | vitrine: sem login, só produtos e variantes ativos; quem gerencia o catálogo vê tudo |
| POST, PATCH | `/produtos`, `/produtos/{id}` | `gerenciar_catalogo` | cria e altera produto |
| POST | `/produtos/{id}/variantes` | `gerenciar_catalogo` | cria variante (cor, tamanho, SKU e preço) |
| PATCH | `/variantes/{id}` | `gerenciar_catalogo` | altera variante; mudar o preço grava o histórico |
| GET | `/variantes/{id}/historico-preco` | `gerenciar_catalogo` | histórico de preço |

### Estoque

A leitura exige alguma permissão da área (movimentar estoque, definir mínimo ou qualquer uma de transferência).

| Método | Rota | Acesso | Descrição |
|---|---|---|---|
| GET | `/estoque` | área de estoque | saldo atual, disponível e mínimo; filtro `abaixo_minimo` |
| GET | `/estoque/historico?em=` | área de estoque | estoque como estava numa data e hora |
| GET | `/estoque/em-transito` | área de estoque | peças enviadas e ainda não recebidas naquele momento |
| GET | `/estoque/evolucao` | área de estoque | série do gráfico de evolução por canal |
| GET | `/estoque/divergencias` | área de estoque | conferência saldo × movimentações (deve vir vazia) |
| PUT | `/estoque/minimo` | `definir_estoque_minimo` | define o estoque mínimo |
| GET | `/movimentacoes-estoque` | área de estoque | movimentações, com filtros |
| POST | `/movimentacoes-estoque` | `movimentar_estoque` | registra saldo inicial, recebimento, avaria, perda ou ajuste |
| POST | `/realocacoes` | `movimentar_estoque` | passa peças entre os canais loja física e online da mesma unidade |

### Transferências

| Método | Rota | Acesso | Descrição |
|---|---|---|---|
| GET | `/transferencias`, `/transferencias/{id}` | qualquer permissão de transferência | lista e detalhe |
| POST | `/transferencias` | `solicitar_transferencia` | solicita |
| POST | `/transferencias/{id}/enviar` | `enviar_transferencia` | envia (saída na origem) |
| POST | `/transferencias/{id}/receber` | `receber_transferencia` | recebe (entrada no destino) |
| POST | `/transferencias/{id}/cancelar` | `solicitar_transferencia` | cancela antes do envio, com motivo |

### Atendimento

| Método | Rota | Acesso | Descrição |
|---|---|---|---|
| POST, GET | `/chamados` | cliente | abre chamado e lista os próprios |
| GET | `/chamados/{id}` | cliente | detalhe do próprio chamado |
| GET, POST | `/chamados/{id}/mensagens` | cliente | mensagens (sem as internas) e resposta |
| GET | `/atendimento/chamados` | `atender_chamado` | fila, com filtros: sem responsável, meus, com mensagem nova… |
| GET, PATCH | `/atendimento/chamados/{id}` | `atender_chamado` | detalhe; o responsável define a prioridade ou repassa o chamado |
| POST | `/atendimento/chamados/{id}/assumir` | `atender_chamado` | assume o chamado |
| POST | `/atendimento/chamados/{id}/concluir` | `atender_chamado` | conclui com motivo |
| GET, POST | `/atendimento/chamados/{id}/mensagens` | `atender_chamado` | mensagens (inclusive internas) e resposta |
| GET | `/atendimento/chamados/{id}/historico` | `atender_chamado` | histórico de status, responsável e prioridade |

### Gestão

As escritas são só do Admin; a lista de unidades é pública porque a vitrine e os filtros também a usam.

| Método | Rota | Acesso | Descrição |
|---|---|---|---|
| GET | `/unidades`, `/unidades/{id}` | público | lojas e CDs |
| POST, PATCH | `/unidades`, `/unidades/{id}` | `gerenciar_unidades` | cria e altera unidade (desativar em vez de apagar) |
| GET | `/permissoes` | `gerenciar_modelos_acesso` | os 15 códigos de permissão |
| GET, POST | `/modelos-acesso` | `gerenciar_modelos_acesso` | lista e cria modelo |
| GET, PATCH | `/modelos-acesso/{id}` | `gerenciar_modelos_acesso` | detalhe; altera nome ou ativo |
| PUT | `/modelos-acesso/{id}/permissoes` | `gerenciar_modelos_acesso` | troca as permissões do modelo |
| GET, POST | `/usuarios` | `gerenciar_contas` | lista contas; cria conta interna (convite ou senha provisória) |
| GET, PATCH | `/usuarios/{id}` | `gerenciar_contas` | detalhe; altera nome, modelo, unidade ou status (inativa bloqueia o login) |
| POST | `/usuarios/{id}/reenviar-convite` | `gerenciar_contas` | reenvia o convite (vale 24h) |
| PUT, DELETE | `/usuarios/{id}/excecoes/{codigo}` | `gerenciar_contas` | acrescenta, retira ou remove exceção de permissão |

## Convenções da API

- **Listas:** listagens grandes são paginadas com `limit` (padrão 50, máximo 200) e `offset`, e respondem `{"items": [...], "total", "limit", "offset"}`. Listas curtas respondem só `{"items": [...]}`.
- **Datas:** gravadas em `timestamptz`. Nos filtros, `AAAA-MM-DD` significa o fim do dia e `AAAA-MM-DDTHH:MM` uma hora exata, no horário de Brasília.
- **Erros:** sempre no formato `{"detail": "..."}`.

| Status | Quando |
|---|---|
| 401 | sem token ou token inválido |
| 403 | conta não cadastrada, não ativa ou sem permissão |
| 404 | registro não encontrado |
| 409 | registro já existe (ex.: e-mail ou CPF já cadastrado) |
| 422 | dados inválidos ou regra de negócio recusada (ex.: estoque insuficiente) |
| 500 | erro inesperado, com mensagem genérica |
| 503 | banco ou Supabase indisponível |

Erros de restrição do banco (CHECK, UNIQUE, FK e triggers) viram 4xx com mensagem genérica; o detalhe técnico fica só no log (`src/middlewares/erros.py`).

- **Logs:** cada requisição ganha um id, que aparece nos logs e no header de resposta `X-Request-ID`. Para investigar um erro, procure esse id nos logs do Render.

## Testes

Os testes ficam em `tests/` e não precisam de banco, internet nem `.env`: o banco e o Supabase são substituídos por versões falsas (`tests/conftest.py` e `tests/apoio.py`).

```powershell
pytest            # roda todos os testes
pytest -v         # mostra o resultado de cada teste
pytest tests/test_estoque.py   # roda só um arquivo
```

Cada área tem seu arquivo (`test_estoque.py`, `test_atendimento.py`, `test_usuarios.py`…), além dos testes de autenticação, permissões, CORS, erros e scripts. Rode os testes antes de cada commit; toda função nova ganha um teste. As garantias que só existem no banco de verdade (RLS, triggers, usuário restrito) são conferidas pelo `scripts/conferir_banco.py`.

**No VS Code (sem terminal):** o repositório já traz a configuração em `.vscode/`. Instale a extensão **Python**, escolha o interpretador do `.venv` (`Ctrl+Shift+P` → **Python: Select Interpreter**) e use o painel **Testing** (ícone de frasco) para rodar todos os testes ou um só.

**No GitHub (CI):** o GitHub Actions roda o pytest a cada push na `main` e em todo Pull Request para a `main` (`.github/workflows/testes.yml`). O resultado aparece no fim da página do PR: vermelho significa que o PR não deve ser mergeado antes da correção. Também dá para rodar pela aba **Actions** → **Testes** → **Run workflow**.

## Deploy (Render)

A API é publicada no Render a cada merge na `main`.

| Configuração | Valor |
|---|---|
| Build command | `pip install -r requirements.txt` |
| Start command | `uvicorn src.app:app --host 0.0.0.0 --port $PORT` |
| Variáveis de ambiente | `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_PUBLISHABLE_KEY`, `DATABASE_URL` (usuário `api_casalorenzi`), `CORS_ORIGINS` (e `CORS_ORIGIN_REGEX`, se usar previews), `PYTHON_VERSION=3.14.3` |
| Health check path | `/docs` (não use `/health`: o Render chama o health check com frequência e manteria conexões abertas no banco) |

`DATABASE_URL_DIRECT` e `DEMO_SENHA` não vão para o Render: migrations e scripts rodam a partir da máquina de quem desenvolve. Se o Python mudar no Render, atualize também o `testes.yml`.

## Integração com o frontend

- **URL base:** `https://grupo13-backend-megw.onrender.com` em produção e `http://127.0.0.1:8000` localmente. No frontend (Vite), fica numa variável de ambiente, como `VITE_API_URL`.
- **Login:** o frontend faz login no Supabase Auth (ou por CPF, em `POST /login/cpf`) e envia o token em toda rota protegida, no header `Authorization: Bearer <access_token>`. A API não usa cookies.
- **Tela inicial:** depois do login, `GET /me` diz o tipo de conta (decide entre plataforma interna e do cliente) e as permissões efetivas (decide quais blocos e abas aparecem).
- **CORS:** o navegador só deixa o frontend chamar a API se o endereço dele estiver em `CORS_ORIGINS`, sem `/` no final. Para os previews da Vercel, use `CORS_ORIGIN_REGEX`, por exemplo `https://<projeto>-.*\.vercel\.app`.

## Estado atual

**Implementado e publicado:**

- contas: cadastro de cliente, login por e-mail (Supabase) ou CPF, perfil com permissões efetivas;
- gestão: contas internas, modelos de acesso, exceções de permissão e unidades;
- catálogo: categorias, produtos, variantes e histórico de preço, com vitrine pública;
- estoque: saldo por unidade e canal, movimentações, realocação, estoque mínimo, histórico em qualquer data, peças em trânsito, gráfico de evolução e conferência de divergências;
- transferências com as etapas solicitada → enviada → recebida e cancelamento;
- atendimento: chamados do cliente, fila da equipe, assumir, prioridade, mensagens internas, conclusão e histórico;
- banco com 27 tabelas, triggers, RLS e usuário restrito para a API; 352 testes automatizados com CI.

**Modelado no banco, ainda sem rotas:** vendas (pedidos, checkout com reserva, pagamentos e estornos), endereços do cliente e avaliações. As tabelas e regras já existem nas migrations e no [case](docs/case/Casa_Lorenzi_Case_Completo.md).

**Planejado:** fotos de produto e anexos de chamado no Supabase Storage; ativação de conta criada no caixa.

## Documentação do projeto

| Documento | Conteúdo |
|---|---|
| [docs/case/Casa_Lorenzi_Case_Completo.md](docs/case/Casa_Lorenzi_Case_Completo.md) | referência do case: contexto, regras de negócio, permissões e modelo de dados |
| [CONTEXT.md](CONTEXT.md) | glossário dos termos do domínio |
| [docs/adr/](docs/adr/) | decisões de arquitetura difíceis de reverter (ADRs 0001 a 0010) |
| [ALEMBIC_GUIDE.md](ALEMBIC_GUIDE.md) | como criar e aplicar migrations |

## Como contribuir

1. Atualize a `main` (`git pull`) e crie uma branch com nome curto em kebab-case, sem prefixo (ex.: `adiciona-avaliacoes`).
2. Siga as camadas: rota em `routes/`, regra em `use_cases/`, acesso ao banco só em `repositories/`.
3. Mudou o banco? Crie a migration conforme o [ALEMBIC_GUIDE.md](ALEMBIC_GUIDE.md) e commite junto com o código.
4. Escreva os testes e rode `pytest` antes de commitar.
5. Commits no padrão Conventional Commits, no imperativo e curtos (`feat: adiciona rota de avaliações`, `fix: ...`, `docs: ...`, `test: ...`).
6. Abra um Pull Request para a `main`; só mergeie com o CI verde.

## Problemas comuns

| Erro | Causa provável |
|---|---|
| `ModuleNotFoundError` (fastapi, jwt…) | ambiente virtual não ativado ou dependências não instaladas |
| `No module named 'src'` | Uvicorn ou script executado fora da raiz do repositório (scripts rodam com `python -m scripts.<nome>`) |
| `Invalid JWKS URI scheme` ao iniciar | `.env` ausente ou `SUPABASE_URL` vazia |
| `DATABASE_URL não definida no .env` / `DATABASE_URL_DIRECT não definida no .env` | variável do banco ausente no `.env` |
| `permission denied for table ...` | a API está conectada com o usuário restrito e a tabela nova não recebeu `GRANT`; veja o [ALEMBIC_GUIDE.md](ALEMBIC_GUIDE.md) |
| 403 `Usuário não cadastrado` | o login existe no Supabase Auth, mas não há linha em `usuario` (contas são criadas pelo backend) |
| No navegador: `blocked by CORS policy` | o endereço do frontend não está em `CORS_ORIGINS` (confira se não sobrou `/` no final) |
| Primeira chamada à API publicada demora ~1 minuto | o Render estava dormindo; as próximas respondem normalmente |
| `DLL load failed while importing pq: Uma política de Controle de Aplicativo bloqueou este arquivo` | o Smart App Control do Windows bloqueou o driver psycopg; a API, o Alembic e os scripts não rodam nessa máquina enquanto o bloqueio existir; os testes continuam rodando no GitHub (CI) |
| `DLL load failed ... nome do arquivo ou a extensão é muito grande` | caminho da pasta longo demais para o Windows; clone o repositório (ou crie o venv) em um caminho mais curto |
| Alembic trava ou não conecta pela conexão direta | a conexão direta do Supabase usa IPv6; se sua rede não tiver, use o Session pooler (porta 5432) em `DATABASE_URL_DIRECT` |
| 401 `Token ausente` / `Token inválido` | header `Authorization` não enviado, token expirado ou de outro projeto |
