# Casa Lorenzi — Backend

API da plataforma de estoque, vendas, catálogo, atendimento e avaliações da Casa Lorenzi, desenvolvida no Case Tech da Trainee Insper Jr 2026.2. Atende duas frentes com entrada única: a **plataforma interna** (equipe da loja) e a **plataforma do cliente**. O frontend fica no repositório [Grupo13-Frontend](https://github.com/nicolels1/Grupo13-Frontend).

- **API publicada (URL base):** https://grupo13-backend-megw.onrender.com (a raiz não tem página e responde 404; use os links abaixo)
- **Documentação interativa:** [Swagger](https://grupo13-backend-megw.onrender.com/docs) e [ReDoc](https://grupo13-backend-megw.onrender.com/redoc)
- **Saúde da API:** [/health](https://grupo13-backend-megw.onrender.com/health)
- **Migração da Vulto (Entrega 3):** [README da migração](migracao_vulto/README.md), [os três números](migracao_vulto/numeros.md), [registro de decisões](migracao_vulto/registro_decisoes.md) e [validação](migracao_vulto/validacao.md)
- **Assistente do cliente (Entrega 4):** [fluxos do n8n e como importar](n8n/README.md)

> No plano gratuito do Render, a API dorme depois de 15 minutos sem uso. A primeira chamada depois disso leva cerca de 1 minuto; as seguintes respondem normalmente.

## Sumário

- [Arquitetura e stack](#arquitetura-e-stack)
- [Estrutura do backend](#estrutura-do-backend)
- [Autenticação e permissões](#autenticação-e-permissões)
- [Banco de dados](#banco-de-dados)
- [Como rodar localmente](#como-rodar-localmente)
- [Scripts](#scripts)
- [Migração da Vulto](#migração-da-vulto)
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
                                           ├── cria logins no Supabase Auth (cadastro e Gestão)
                                           └── envia e lê arquivos no Supabase Storage
```

O banco também cancela sozinho reservas e retiradas vencidas: o pg_cron roda uma função a cada minuto, mesmo com a API dormindo no Render (ADR 0012).

O Supabase fornece a identidade (Auth), o banco (PostgreSQL) e os arquivos (Storage). Todo acesso a dados e arquivos passa pelo FastAPI: o frontend nunca fala direto com o banco nem envia arquivos ao Storage, e as tabelas ficam fechadas para a API automática do Supabase.

| Tecnologia | Função |
|---|---|
| Python 3.14 + FastAPI | API |
| Uvicorn | servidor |
| Pydantic | validação de entrada e saída |
| SQLAlchemy + psycopg | ORM e driver do PostgreSQL |
| Alembic | migrations |
| PostgreSQL (Supabase) | banco de dados, com triggers, RLS e pg_cron |
| Supabase Auth + PyJWT | login e validação do token |
| Supabase Storage | fotos de produto, anexos de chamado e fotos de avaliação |
| httpx2 | chamadas às APIs de administração do Supabase (Auth e Storage) |
| python-multipart | recebimento de arquivos (multipart/form-data) |
| pytest + GitHub Actions | testes automatizados e CI |
| Render | deploy |

## Estrutura do backend

```
src/
├── app.py          # cria o app, registra middlewares e rotas
├── config/         # settings.py (variáveis do .env) e logs.py (logs com id da requisição)
├── database/       # base.py (Base dos models) e session.py (engine, sessão e get_db)
├── entities/       # schemas Pydantic de entrada e saída de cada área
├── models/         # tabelas SQLAlchemy (36 tabelas: as 27 do modelo de dados, os destaques do carrossel, as 2 da migração da Vulto, as 5 da fidelidade e a compra do site)
├── repositories/   # única camada que consulta e grava no banco
├── use_cases/      # regras de negócio
├── routes/         # rotas FastAPI, uma por área
├── dependencies/   # usadas rota a rota com Depends: auth.py (token), permissoes.py (conta ativa e permissões)
│                   # e supabase.py (clientes do Auth e do Storage, trocados por falsos nos testes)
├── middlewares/    # valem para toda requisição: cors.py, requisicao.py (id e erro 500) e erros.py (erros → 4xx)
└── utils/          # cpf.py, supabase_admin.py (Auth), supabase_storage.py (Storage) e upload.py
migrations/         # Alembic: env.py e versions/ (fora do src/: roda na máquina de quem desenvolve, não na API)
scripts/            # criar_admin, carregar_demo e conferir_banco
migracao_vulto/     # migração dos dados da Vulto: scripts, notebooks e registro de decisões
tests/              # testes do pytest
docs/               # case, ADRs
requirements.txt    # dependências da API (o que o Render instala)
requirements-dev.txt  # as da API mais as de teste (pytest), para quem desenvolve e para o CI
```

Caminho de uma requisição:

```
rota (routes/) → token (dependencies/auth.py) → conta ativa e permissão (dependencies/permissoes.py)
   → regra de negócio (use_cases/) → banco (repositories/ → models/ → PostgreSQL)
```

## Autenticação e permissões

**Autenticação (quem é).** O login acontece no Supabase Auth. Cada rota protegida recebe o header `Authorization: Bearer <access_token>`; o backend valida a assinatura com a chave pública do projeto (`SUPABASE_URL/auth/v1/.well-known/jwks.json`, algoritmo ES256, audience `authenticated`). O cliente também pode entrar com **CPF e senha** pela rota `POST /login/cpf`: o backend troca o CPF pela sessão do Supabase sem expor o e-mail.

**Autorização (o que pode).** Tipo de conta e permissões ficam no banco, nunca no token (ADR 0001), e são lidos a cada requisição: mudanças valem na hora.

- Toda conta tem tipo `interna` ou `cliente` e status `ativa`, `pendente_ativacao` ou `inativa`. Só conta ativa usa a API.
- A conta interna está ligada a um **modelo de acesso** (Admin, Funcionário, Estoquista, Atendente…) e pode ter **exceções** que acrescentam ou retiram permissões.
- O Admin tem todas as permissões. As permissões da Gestão (`gerenciar_contas`, `gerenciar_modelos_acesso`, `gerenciar_unidades`) são só dele, garantido por trigger (ADRs 0009 e 0010).
- São 19 códigos de permissão: os 16 da seção 6 do [case](docs/case/Casa_Lorenzi_Case_Completo.md) (com a tabela por modelo), mais `ver_marca_lorenzi`, `ver_marca_vulto` e `gerenciar_fidelidade`, que entraram com a Entrega 3.

Dependências prontas para proteger uma rota (`src/dependencies/permissoes.py`):

```python
from src.dependencies.permissoes import exige_permissao, exige_cliente, get_usuario_ativo

usuario: Usuario = Depends(exige_permissao("movimentar_estoque"))  # conta interna com a permissão
cliente: Usuario = Depends(exige_cliente)                          # conta de cliente ativa
usuario: Usuario = Depends(get_usuario_ativo)                      # qualquer conta ativa
```

Respostas: **401** sem token ou com token inválido; **403** conta não cadastrada, não ativa ou sem permissão; **503** se o Supabase não responder.

**Cadastro.** Toda conta é criada pelo backend, que cria o login no Supabase Auth e a linha em `usuario` juntos (ADR 0008); o cadastro aberto no Auth fica desligado. O cliente se cadastra por `POST /clientes`; contas internas são criadas pelo Admin na Gestão, por convite ou com senha provisória. O caixa da loja não cria conta: o CPF informado na venda fica no pedido (`cpf_nota`) e, quando a pessoa se cadastra pelo site com ele, as compras passam para a conta nova (ADR 0014). O login por CPF responde "CPF ou senha incorretos" tanto para CPF sem conta quanto para senha errada.

## Banco de dados

O backend usa duas conexões com o PostgreSQL do Supabase:

| Variável | Usuário | Uso |
|---|---|---|
| `DATABASE_URL` | `api_casalorenzi` (restrito), Transaction pooler, porta 6543 | a API e os scripts |
| `DATABASE_URL_DIRECT` | dono do banco, Direct connection, porta 5432 | só as migrations do Alembic |

**Duas camadas de proteção**, que valem para qualquer caminho, inclusive o painel do Supabase:

- **Usuário restrito da API** (`api_casalorenzi`, migration `56799f788354`): não apaga dados (exceto endereços salvos do cliente, as ligações da Gestão, fotos de produto e slides do carrossel), não edita movimentações nem históricos, não altera o saldo do estoque direto e não lê o `auth.users`. Todas as tabelas têm RLS ligado, com uma regra que libera só ele.
- **Triggers e restrições:** o saldo de `estoque` é atualizado pelo banco a cada movimentação e nunca fica negativo (ADR 0005); movimentações e históricos não são editados; as regras do Admin, do CD, do estorno e do item do chamado são garantidas no banco. Toda troca e devolução tem o pedido e o chamado ou a pessoa da equipe, e todo estorno tem origem (`cancelamento`, `atendimento` ou `balcao`, ADR 0015).

**Busca da vitrine** (migration `5809c1f5a848`): liga as extensões `unaccent` e `pg_trgm` (esquema `extensions`) e cria `texto_de_busca()` (sem acento e minúsculo) e `semelhanca_de_palavra()` (semelhança entre palavras inteiras), funções do dono liberadas só para o usuário da API, como `login_por_email`.

**Prazos automáticos** (migration `03aeb347f6cf`, ADR 0012): a função `cancela_vencidos()` cancela o pedido cuja reserva de 15 minutos venceu (as peças voltam ao disponível e a cobrança pendente é recusada) e a retirada não feita em 7 dias (estorno pelo mesmo método e as peças voltam ao estoque online). O pg_cron a roda a cada minuto.

**Arquivos** no Supabase Storage, em três buckets criados pela mesma migration: `produtos` (público, fotos de produto), `anexos` (privado, anexos de chamado) e `avaliacoes` (privado, fotos de avaliação). O backend confere tipo, conteúdo e tamanho antes de enviar, guarda só o caminho no banco e devolve link temporário (1 hora) para os arquivos privados.

O histórico de estoque é calculado somando as movimentações até a data pedida, sem snapshot (ADR 0006). A rota `GET /estoque/divergencias` lista as linhas em que o saldo não bate com as movimentações e deve vir sempre vazia.

**Transações.** Cada requisição usa uma sessão (`get_db`), e a transação começa na primeira consulta. As regras para quem escreve um use case:

- **O use case faz o `commit`**, uma vez por requisição; rotas e repositórios nunca fazem. Função auxiliar que faz commit só é chamada como último passo (`return _auxiliar(...)`). Se der erro antes do commit, nada fica gravado: o fechamento da sessão desfaz tudo.
- **Linha travada (`travar=True`) fica travada até o commit.** Por isso, nada de upload ou chamada ao Supabase com trava aberta: confira, encerre a transação e confira de novo depois da chamada (exemplo: `adicionar_foto` em `use_cases/avaliacoes.py`).
- **Chamada externa que pode falhar precisa de plano para a falha**, porque o banco não desfaz o que já foi feito no Supabase:
  - o que é criado fora antes da gravação (login no Auth, arquivo no Storage) é desfeito se a gravação falhar (`apagar_login`, `arquivos.desfazer_envio`);
  - o que muda algo fora depois da gravação (bloquear login, apagar arquivo antigo) vem depois do commit; se falhar, o banco volta ao valor anterior (`usuarios.alterar`) ou o problema fica registrado no log.

O modelo de dados completo (36 tabelas e valores aceitos) está na seção 7 do [case](docs/case/Casa_Lorenzi_Case_Completo.md): as 27 do modelo da Entrega 2, a tabela `destaque` (slides do carrossel do topo da página inicial, migration `91a5ddfd1a16`), para a equipe trocar o carrossel pelo interno em vez de pelo código, as 7 tabelas da Entrega 3, `cliente_sem_conta` e `origem_migracao` (migração da Vulto) e `regra_fidelidade`, `movimento_pontos`, `saldo_pontos`, `lote_pontos` e `consumo_pontos` (fidelidade), e a `compra` (carrinho único das duas marcas, com `pedido.id_compra`, migration `8834803e1dc8`, [ADR 0017](docs/adr/0017-carrinho-unico-um-pedido-por-marca.md)). Comandos do Alembic, passo a passo para mudar o banco e as regras das migrations estão no [ALEMBIC_GUIDE.md](ALEMBIC_GUIDE.md).

## Como rodar localmente

Os comandos rodam na raiz do repositório (a pasta que contém `src/`).

**Pré-requisito:** Python 3.14 (testado com o 3.14.3, a mesma versão do Render e do CI).

```powershell
# 1. Criar e ativar o ambiente virtual
python -m venv .venv
.\.venv\Scripts\Activate.ps1        # macOS/Linux: source .venv/bin/activate

# 2. Instalar as dependências (as da API e as de teste; o Render instala só o requirements.txt)
pip install -r requirements-dev.txt

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
| `VULTO_DATA_DIR` | não | pasta `data/` do vulto-legado-back, lida pela [migração da Vulto](#migração-da-vulto); sem ela, o script pede `--origem` |

Nas URLs do banco, codifique caracteres especiais da senha (`@` vira `%40`). O `.env.example` mostra o formato completo. O `.env` está no `.gitignore` e nunca deve ser commitado; peça os valores a quem mantém o projeto Supabase.

## Scripts

Rodam na raiz do repositório, com o venv ativo e o `.env` preenchido:

| Comando | O que faz |
|---|---|
| `python -m scripts.criar_admin` | cria a primeira conta Admin (login no Supabase Auth + linha em `usuario`). Pede nome, e-mail e senha |
| `python -m scripts.carregar_demo` | carrega o cenário de demonstração: unidades, catálogo, modelos de acesso, contas internas e de cliente, 30 dias de movimentações de estoque e pedidos em cada etapa (venda na loja com e sem CPF, entrega paga para preparar, retirada pronta, pedido entregue com avaliação, voto útil e denúncia pendente, e uma venda com CPF na nota de alguém sem conta). Também cria o modelo Vendedor, com uma conta na Loja Pinheiros, e completa as permissões dos modelos que já existiam. Imprime os códigos de venda e esse CPF. Pode rodar de novo sem duplicar nada |
| `python -m scripts.conferir_banco` | confere no banco de verdade as garantias do usuário restrito, do RLS, do saldo do estoque, da troca e devolução sempre ligadas ao pedido e do cancelamento da reserva vencida. Roda numa transação desfeita no final: nada fica gravado |

## Migração da Vulto

A pasta [`migracao_vulto/`](migracao_vulto/) leva os dados da Vulto (banco SQLite e planilhas do vulto-legado-back) para o banco da Casa Lorenzi, em etapas: padronização automática, tratamento com as decisões de negócio e carga. Os dados de origem não ficam neste repositório (têm dados pessoais de clientes): o caminho vem de `VULTO_DATA_DIR`. Como rodar e o que cada etapa faz estão no [README da migração](migracao_vulto/README.md); as decisões tomadas sobre os dados, no [registro de decisões](migracao_vulto/registro_decisoes.md); os três números da Vulto (faturamento, estoque e passivo de pontos), com a margem de erro de cada um, no [numeros.md](migracao_vulto/numeros.md); e a conferência do que entrou no banco, na [validação](migracao_vulto/validacao.md).

O que a plataforma ganhou para receber a Vulto (cada item cita a decisão do registro):

- **marca** (`lorenzi` ou `vulto`) na unidade e no produto (P1); a vitrine filtra por marca, e a marca só se corrige enquanto não há movimentação nem venda. Cada unidade só guarda peças da própria marca: entrada, estoque inicial, mínimo, transferência (só entre unidades da mesma marca), troca e devolução recusam a peça da outra. O Admin decide que marca cada pessoa da equipe vê com as permissões `ver_marca_lorenzi` e `ver_marca_vulto` (no modelo ou como exceção): nas listas (estoque, transferências, pedidos, chamados, balcão, regras e extrato de pontos e Visão Geral), quem vê uma marca só recebe essa e não consegue pedir a outra (403); o passivo e os pontos de um cliente mostram só as marcas que a pessoa vê. Abrir ou mudar um registro de outra marca também dá 403: pedido, chamado, transferência, produto, variante e foto pelo id, e movimentação, mínimo, realocação, venda na loja, transferência, produto novo, regra de pontos e resgate no caixa pela unidade ou marca informada. O chamado sem marca (dúvida geral) abre para todos, e a moderação de avaliações vale para a rede inteira. Os modelos que existiam antes ganharam as duas;
- **EAN** (código de barras) obrigatório na variante (C12): o de fábrica ou um código interno gerado pelo banco (prefixo 20); o caixa acha a peça pelo leitor;
- **cliente sem conta** (P2, [ADR 0016](docs/adr/0016-cliente-sem-conta-da-migracao.md)): pedidos e chamados de quem nunca criou conta, que passam para a conta quando a pessoa se cadastra com o mesmo CPF ou e-mail;
- **origem da migração** (P4): de onde veio cada registro migrado e o que a migração mudou nele, com a decisão aplicada;
- **canal de venda** (`loja`, `site` ou `app`) no pedido (P3), para o faturamento por canal sem mexer no canal do estoque; os pedidos que já existiam ficaram com `loja` ou `site`;
- **desconto** no pedido (V18): total = itens − desconto + frete; zero nos pedidos da Lorenzi;
- **pagamento migrado** (P7): método `nao_informado` e origem `migracao`, aceitos só no pagamento e no estorno migrados; o estorno de um pagamento sem método sai pelo mesmo método, sem gateway;
- **prazo de troca do pedido migrado** (V13): o pedido online da Vulto, sem data de entrega, conta os 30 dias do pagamento;
- **atendimento externo** (P6): o chamado migrado guarda o canal (`whatsapp`, `instagram` ou `email`) e o contato de quem escreveu; a fala do cliente entra sem autor, com o nome de quem falou, e conta como do cliente nas mensagens, nas não lidas e na primeira resposta da Visão Geral;
- **categoria `pagamento`** no chamado (A4a), também para os chamados novos;
- **fidelidade** (P5, K3, K4a, K4c): regras por marca com data, extrato de pontos que nunca muda, saldo e categoria por cliente em cada marca e lotes com o valor da época e o vencimento; o trigger aplica o FIFO, sobe a categoria e vira o ano, e o pg_cron vence os pontos (todo dia) e vira o ano de quem não teve movimento (1º de janeiro). A Lorenzi começa com a mesma regra da Vulto, a partir de 01/10/2026, e quem tem a permissão nova `gerenciar_fidelidade` (no começo, só o Admin) cria as regras seguintes de cada marca.

Próximos passos, fora da Entrega 3:

- **mudança de rede com data:** hoje a marca é a atual. Se uma loja mudar de rede, um histórico de marca com data (como o de preço) mantém o passado na rede antiga; dá para criar depois sem refazer nada;
- **notificações por e-mail e consentimento na conta:** a plataforma não manda e-mail da loja. O consentimento de mensagem dos clientes da Vulto está guardado (K9b); antes de qualquer campanha, a conta da Lorenzi também precisa perguntar o consentimento;
- **EAN das 9 peças da Vulto cortado pelo Excel:** entram com o código interno; a loja corrige lendo a etiqueta (`PATCH /variantes/{id}`);
- **programa de pontos único da rede:** hoje cada marca tem o seu saldo (P10); se a diretoria quiser unificar, basta uma regra que valha para a rede, sem refazer o histórico;
- **pontos na junção de contas:** quando a correção de CPF junta duas contas, os pedidos passam para a conta certa, mas os pontos da conta errada não; levar os pontos exige registrar qual conta foi juntada a qual.

## Rotas

A lista completa, com parâmetros e formatos, está em `/docs`. Resumo por área:

### Saúde e contas

| Método | Rota | Acesso | Descrição |
|---|---|---|---|
| GET | `/health` | público | consulta o banco; 503 se ele não responder |
| POST | `/clientes` | público | cadastro de cliente (nome, e-mail, CPF, senha e `aceita_lembrete_pontos`, que vem desmarcado); as compras da loja com esse CPF na nota e o histórico do cliente sem conta com esse CPF ou e-mail passam para a conta (`compras_ligadas`), com o saldo de pontos dele |
| POST | `/login/cpf` | público | login do cliente por CPF e senha; devolve a sessão do Supabase |
| GET | `/me` | conta ativa | perfil, tipo de conta e permissões efetivas (monta a tela inicial) |
| GET | `/visao-geral/faturamento?de=&ate=&id_unidade=&marca=` | Admin | faturamento por mês, unidade e canal da venda, com o frete à parte: a mesma regra dos três números da Vulto |
| GET | `/visao-geral/resumo?id_unidade=&marca=&referencia=` | conta interna | números e gráficos da Visão Geral; só as seções que a conta pode ver (as outras ficam ausentes) |

### Catálogo

| Método | Rota | Acesso | Descrição |
|---|---|---|---|
| GET | `/categorias` | público | categorias, com `imagem_url` (foto do carrossel da página inicial; vazio sem foto); `?marca=` traz só as que têm produto ativo da rede |
| POST, PATCH | `/categorias`, `/categorias/{id}` | `gerenciar_catalogo` | cria e altera categoria |
| POST, DELETE | `/categorias/{id}/imagem` | `gerenciar_catalogo` | envia ou troca a foto da categoria (multipart: JPG, PNG ou WEBP de até 5 MB, no bucket `produtos`, pasta `categorias/`) ou tira a foto |
| GET | `/destaques` | público | slides do carrossel do topo da página inicial, na ordem: a loja vê os ativos; quem gerencia o catálogo vê também os desativados |
| POST, PATCH, DELETE | `/destaques`, `/destaques/{id}` | `gerenciar_catalogo` | cria, altera (texto, botão, link, ordem, ativo) e apaga slide. O link só aceita página da loja (começa com `/`) |
| POST | `/destaques/{id}/imagem` | `gerenciar_catalogo` | envia ou troca a foto do slide (multipart: JPG, PNG ou WEBP de até 5 MB, no bucket `produtos`, pasta `destaques/`) |
| GET | `/produtos/tamanhos` | público | tamanhos à venda na vitrine, na ordem da grade (filtra por `id_categoria`, `busca` e `marca`) |
| GET | `/produtos`, `/produtos/{id}` | público | vitrine: sem login, só produtos e variantes ativos; quem gerencia o catálogo vê tudo. Cada variante diz se tem peça para vender online (`disponivel`, sem a quantidade). A lista filtra por `tamanho` (repetível: `?tamanho=P&tamanho=M`) e `disponivel` e ordena por `relevancia` (padrão com busca), `nome`, `novidades`, `menor_preco` ou `maior_preco`. A `busca` ignora acentos e procura no nome, categoria, cores e descrição, pelo começo das palavras e por semelhança (erros de digitação, camisa e camiseta); sem resultado, a vitrine traz peças parecidas ou as novidades e avisa em `busca_alternativa`. `?marca=lorenzi` ou `vulto` mostra a vitrine de uma rede; as sugestões da busca vazia vêm das duas |
| POST, PATCH | `/produtos`, `/produtos/{id}` | `gerenciar_catalogo` | cria e altera produto |
| POST | `/produtos/{id}/variantes` | `gerenciar_catalogo` | cria variante (cor, tamanho, SKU, EAN e preço); sem EAN, o banco gera um código interno |
| PATCH | `/variantes/{id}` | `gerenciar_catalogo` | altera variante; mudar o preço grava o histórico; o EAN se troca, mas não fica vazio |
| GET | `/variantes/{id}/historico-preco` | `gerenciar_catalogo` | histórico de preço |
| POST | `/produtos/{id}/imagens` | `gerenciar_catalogo` | envia foto (multipart; sem cor, vale para todas) |
| PATCH, DELETE | `/imagens/{id}` | `gerenciar_catalogo` | muda a cor ou a ordem da foto, ou apaga a foto (e o arquivo no Storage) |

As fotos aparecem em `imagens` de cada produto, com a URL pública.

### Carrinho e pedidos (plataforma do cliente)

| Método | Rota | Acesso | Descrição |
|---|---|---|---|
| POST | `/carrinho` | público | preços do momento, frete da entrega (um só para o carrinho, com o valor mínimo do frete grátis contado sobre todas as peças), lojas com tudo para retirada e o resumo de cada marca em `marcas`; não reserva nada |
| GET, POST | `/enderecos` | cliente | endereços salvos |
| PATCH, DELETE | `/enderecos/{id}` | cliente | altera ou apaga (o pedido guarda a própria cópia) |
| POST | `/pedidos` | cliente | checkout: escolhe a unidade, reserva as peças por 15 minutos e cria o pedido aguardando pagamento. `pontos_resgate` usa pontos como desconto: saem agora e voltam se a reserva vencer ou o pedido for cancelado |
| POST | `/compras` | cliente | checkout do carrinho do site, que junta as duas marcas: uma compra com um pedido por marca, cada um numa unidade dela, tudo ou nada. Com as duas marcas, só entrega em casa. `pontos_por_marca` (ex.: `{"lorenzi": 200, "vulto": 150}`) usa os pontos de cada marca como desconto no pedido dela ([ADR 0017](docs/adr/0017-carrinho-unico-um-pedido-por-marca.md)) |
| GET | `/compras/{id}` | cliente | a compra com os pedidos dela, para o pagamento e a confirmação |
| POST | `/compras/{id}/pagamentos` | cliente | cobrança única da compra (Pix, crédito ou débito); pedir de novo com o mesmo método devolve a mesma |
| POST | `/compras/{id}/simular` | cliente | gateway simulado: `{"aprovado": true}` paga todos os pedidos e baixa o estoque; depois do prazo da reserva, cancela sem cobrar |
| POST | `/compras/{id}/cancelar` | cliente | cancela a compra inteira antes do pagamento e libera as reservas |
| GET | `/pedidos`, `/pedidos/{id}` | cliente | meus pedidos, com itens (cada um com a foto da cor comprada em `foto_url`), pagamentos e endereço de entrega |
| POST | `/pedidos/{id}/pagamentos` | cliente | cria a cobrança do valor que falta (Pix, crédito ou débito) no gateway simulado |
| POST | `/pagamentos/{id}/simular` | cliente | gateway simulado: `{"aprovado": true}` paga o pedido e baixa o estoque (ADR 0011) |
| POST | `/pedidos/{id}/cancelar` | cliente | cancela antes do pagamento e libera a reserva (pedido de compra cancela pela compra) |
| POST | `/pedidos/reivindicar` | cliente | liga à conta uma compra feita na loja sem CPF, pelo código do comprovante |

### Vendas (plataforma interna)

| Método | Rota | Acesso | Descrição |
|---|---|---|---|
| GET | `/vendas/pedidos`, `/vendas/pedidos/{id}` | qualquer permissão de vendas ou `atender_chamado` | pedidos, com filtros: status, canal, modalidade, unidade, `marca` (a da unidade), código, `pronto_ha_mais_de_dias` e data de criação (`de`, `ate`; data sem hora vale o dia inteiro) |
| POST | `/vendas/pedidos` | `registrar_venda_fisica` | venda na loja: nasce paga e entregue, baixa o estoque de loja física. CPF opcional (`cpf_nota`): com conta, liga o pedido; de um cliente da Vulto que ainda não criou conta, liga a ele (e ele ganha os pontos); sem nenhum dos dois, fica guardado no pedido. `pontos_resgate` usa os pontos desse cliente como desconto, e os pagamentos fecham com o total menos o desconto |
| POST | `/vendas/pedidos/{id}/enviar` | `preparar_entregar_pedido` | entrega em casa paga → enviada |
| POST | `/vendas/pedidos/{id}/pronto-retirada` | `preparar_entregar_pedido` | retirada paga → pronta (começa o prazo de 7 dias) |
| POST | `/vendas/pedidos/{id}/entregar` | `preparar_entregar_pedido` | entrega o pedido enviado ou retirado (na retirada, confere o código) |
| POST | `/vendas/pedidos/{id}/cancelar` | `cancelar_pedido_equipe` | antes do envio, com justificativa; se já pago, estorna tudo e devolve as peças ao estoque. Pedido de compra do site ainda sem pagamento cancela a compra inteira |
| GET | `/vendas/clientes?cpf=` | `registrar_venda_fisica` ou `corrigir_cadastro_cliente` | mostra no caixa se o CPF já tem conta |
| PATCH | `/vendas/clientes/{id}` | `corrigir_cadastro_cliente` | corrige e-mail ou CPF; CPF que já tem conta junta os pedidos nela, e as compras com o CPF certo na nota vão para a conta |
| GET | `/vendas/clientes-sem-conta?busca=`, `/vendas/clientes-sem-conta/{id}` | qualquer permissão de vendas ou `atender_chamado` | clientes da Vulto que ainda não criaram conta: CPF ou telefone (só números) ou parte do nome ou e-mail. Os pedidos e chamados deles saem em `/vendas/pedidos` e `/atendimento/chamados` com `?id_cliente_sem_conta=` |
| GET | `/migracao/origem?tabela=&id_registro=` | quem vê pedido, chamado, peça ou estoque | de onde veio um registro migrado da Vulto e o que a migração mudou nele, com a decisão do registro |

### Estoque

A leitura exige alguma permissão da área (movimentar estoque, definir mínimo ou qualquer uma de transferência). As listas de estoque, histórico, em trânsito e movimentações aceitam `?marca=lorenzi|vulto` (a marca da unidade), dentro das marcas que a pessoa vê.

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
| GET | `/transferencias`, `/transferencias/{id}` | qualquer permissão de transferência | lista e detalhe; a lista filtra por `status`, `id_unidade` e `marca` |
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
| GET | `/atendimento/chamados` | `atender_chamado` | fila, com filtros: sem responsável, meus, com mensagem nova, `marca`… |
| GET, PATCH | `/atendimento/chamados/{id}` | `atender_chamado` | detalhe; o responsável define a prioridade ou repassa o chamado |
| GET | `/atendimento/equipe` | `atender_chamado` | contas ativas que atendem chamados, por nome: para quem dá para repassar |
| POST | `/atendimento/chamados/{id}/assumir` | `atender_chamado` | assume o chamado |
| POST | `/atendimento/chamados/{id}/concluir` | `atender_chamado` | conclui com motivo |
| GET, POST | `/atendimento/chamados/{id}/mensagens` | `atender_chamado` | mensagens (inclusive internas) e resposta |
| GET | `/atendimento/chamados/{id}/historico` | `atender_chamado` | histórico de status, responsável e prioridade |
| POST | `/chamados/{id}/anexos`, `/atendimento/chamados/{id}/anexos` | cliente / `atender_chamado` | mensagem com anexo (multipart: JPG, PNG, WEBP ou PDF de até 10 MB) |
| GET | `/chamados/{id}/mensagens/{id_mensagem}/anexo`, `/atendimento/chamados/{id}/mensagens/{id_mensagem}/anexo` | cliente / `atender_chamado` | link temporário do anexo |
| POST | `/atendimento/chamados/{id}/devolucao` | `atender_chamado` | devolução até 30 dias após a entrega: a peça entra no estoque da loja e o estorno sai do pagamento escolhido |
| POST | `/atendimento/chamados/{id}/troca` | `atender_chamado` | troca por outra cor ou tamanho do mesmo produto, na mesma loja |
| POST | `/atendimento/chamados/{id}/estornos` | `atender_chamado` | estorno sem troca nem devolução (pode ser parcial) |

### Balcão (troca e devolução sem chamado)

| Método | Rota | Acesso | Descrição |
|---|---|---|---|
| GET | `/balcao/pedidos?codigo_venda=` / `?id_pedido=` / `?cpf=` | `registrar_troca_devolucao` | acha o pedido (um filtro só); pelo CPF da conta ou na nota, os entregues nos últimos 30 dias |
| POST | `/balcao/pedidos/{id}/devolucao` | `registrar_troca_devolucao` | a peça entra no estoque de loja física da loja; estorno pelo mesmo meio de pagamento |
| POST | `/balcao/pedidos/{id}/troca` | `registrar_troca_devolucao` | troca por outra cor ou tamanho do mesmo produto, na mesma loja |
| GET | `/balcao/estoque` | `registrar_venda_fisica`, `registrar_troca_devolucao` ou `atender_chamado` | consultar peça: estoque das unidades, só leitura (o atendimento usa na troca pelo chamado) |

Vale para qualquer pedido entregue há no máximo 30 dias, com ou sem conta, e só em loja (nunca no CD). O banco registra quem fez e em que loja (ADR 0015).

### Fidelidade

Cada marca tem o seu programa de pontos (P10). O saldo, a categoria e o vencimento ficam no banco (trigger e pg_cron).

Os pontos entram e saem sozinhos, pelo banco: o pedido de quem tem conta (ou é cliente sem conta da Vulto) ganha pontos quando os pagamentos aprovados cobrem o total, pela regra da marca da unidade, e cada estorno de dinheiro tira a mesma proporção dos pontos do pedido, sem deixar o saldo negativo se o cliente já os gastou. Quando o cliente sem conta cria a conta, o saldo e os lotes passam para ela (somando ao que ela já tinha na marca, com a categoria mais alta). O pagamento migrado da Vulto não mexe nos pontos. A compra só com o CPF na nota ganha os pontos quando passa para a conta (no cadastro), pela data da compra: vale, vence e entra na fila de resgate como se tivesse sido creditada naquele dia, e não ganha nada se esses pontos já teriam vencido ou se a marca ainda não tinha programa de fidelidade.

No resgate, os pontos viram desconto no pedido (`valor_desconto`): saem primeiro os mais antigos, cada um pelo valor da época, e o pedido ganha pontos só sobre o valor pago. Os pontos usados voltam para os mesmos lotes, com a validade deles, quando o pedido é cancelado (por qualquer caminho, inclusive o pg_cron) ou devolvido inteiro; na devolução parcial, o desconto fica na compra.

| Método | Rota | Acesso | Descrição |
|---|---|---|---|
| GET | `/fidelidade/regras?marca=` | `gerenciar_fidelidade` | regras de cada marca, da mais nova para a mais antiga; `vigente` marca a que vale hoje |
| GET | `/fidelidade/meus-pontos` | cliente | saldo em cada marca, com o valor em R$ (cada ponto pela época), a categoria, os pontos ganhos no ano e o próximo vencimento |
| GET | `/fidelidade/meus-pontos/extrato?marca=` | cliente | extrato paginado, do mais novo para o mais antigo, com o histórico de quando era cliente sem conta; nas entradas, o que sobra e quando vence |
| GET, PUT | `/fidelidade/meus-pontos/lembrete` | cliente | se recebe o e-mail duas semanas antes de os pontos vencerem, e liga ou desliga (`{aceita}`); escolher pelo site conta como oferecido, e o assistente não oferece de novo |
| GET | `/fidelidade/clientes/pontos?id_cliente=` ou `?id_cliente_sem_conta=`, `/fidelidade/clientes/pontos/extrato` | `atender_chamado`, `registrar_venda_fisica` ou `gerenciar_fidelidade` | o mesmo, de um cliente, para o atendimento ("meus pontos sumiram"), o caixa e quem cuida do programa |
| GET | `/fidelidade/passivo?em=` | `gerenciar_fidelidade` | quanto cada marca deve em pontos, separado pelo valor do ponto na época em que foi ganho (o passivo da Entrega 3); `em` dá a posição num momento passado, no formato do histórico do estoque |
| GET | `/fidelidade/resgate?marca=&pontos=` | cliente | quanto valem os pontos do cliente num resgate, antes do checkout (`pontos_resgate` no `POST /pedidos` e `pontos_por_marca` no `POST /compras`): os mais antigos primeiro, cada um pelo valor da época (K3); confere o mínimo, o saldo e a regra da marca |
| GET | `/fidelidade/resgate/caixa?cpf=&marca=&pontos=` | `registrar_venda_fisica` | o mesmo no caixa, para o cliente do CPF (com conta ou da Vulto sem conta) |
| POST | `/fidelidade/regras` | `gerenciar_fidelidade` | regra nova a partir de amanhã ou de uma data futura: vale dali em diante e nunca muda um ponto já ganho; uma regra que ainda não começou se corrige com outra para a mesma data (vale a criada por último) |

### Avaliações

| Método | Rota | Acesso | Descrição |
|---|---|---|---|
| GET | `/produtos/{id}/avaliacoes` | público | avaliações publicadas, com a média e a contagem de cada nota; `com_fotos=true` traz só as com foto |
| GET | `/avaliacoes/{id}` | público (oculta: só quem avaliou e a moderação) | detalhe com fotos |
| POST | `/avaliacoes` | cliente | avalia um item de pedido entregue (uma vez por item) |
| PATCH | `/avaliacoes/{id}` | cliente (autor) | edita nos 7 dias após a publicação |
| POST | `/avaliacoes/{id}/fotos` | cliente (autor) | até 5 fotos (multipart) |
| POST | `/avaliacoes/{id}/util` | cliente | voto útil, um por pessoa, não na própria |
| POST | `/avaliacoes/{id}/denuncias` | cliente | denúncia, uma por pessoa, não na própria |
| GET | `/moderacao/avaliacoes` | `moderar_avaliacoes` | todas, com denúncias pendentes |
| POST | `/moderacao/avaliacoes/{id}/ocultar` | `moderar_avaliacoes` | oculta com motivo; as fotos saem do ar |
| GET | `/moderacao/denuncias` | `moderar_avaliacoes` | denúncias (padrão: pendentes) |
| POST | `/moderacao/denuncias/{id}/analisar` | `moderar_avaliacoes` | procedente oculta a avaliação; improcedente só encerra |

### Gestão

As escritas são só do Admin; a lista de unidades é pública porque a vitrine e os filtros também a usam.

| Método | Rota | Acesso | Descrição |
|---|---|---|---|
| GET | `/unidades`, `/unidades/{id}` | público | lojas e CDs |
| POST, PATCH | `/unidades`, `/unidades/{id}` | `gerenciar_unidades` | cria e altera unidade (desativar em vez de apagar) |
| GET | `/permissoes` | `gerenciar_modelos_acesso` | os 19 códigos de permissão |
| GET, POST | `/modelos-acesso` | `gerenciar_modelos_acesso` | lista e cria modelo |
| GET, PATCH | `/modelos-acesso/{id}` | `gerenciar_modelos_acesso` | detalhe; altera nome ou ativo |
| PUT | `/modelos-acesso/{id}/permissoes` | `gerenciar_modelos_acesso` | troca as permissões do modelo |
| GET, POST | `/usuarios` | `gerenciar_contas` | lista contas; cria conta interna (convite ou senha provisória) |
| GET, PATCH | `/usuarios/{id}` | `gerenciar_contas` | detalhe; altera nome, modelo, unidade ou status (inativa bloqueia o login) |
| POST | `/usuarios/{id}/reenviar-convite` | `gerenciar_contas` | reenvia o convite (vale 24h) |
| PUT, DELETE | `/usuarios/{id}/excecoes/{codigo}` | `gerenciar_contas` | acrescenta, retira ou remove exceção de permissão |

## Convenções da API

- **Listas:** listagens grandes são paginadas com `limit` (padrão 50, máximo 200) e `offset`, e respondem `{"items": [...], "total", "limit", "offset"}`. Listas curtas respondem só `{"items": [...]}`.
- **Arquivos:** enviados em `multipart/form-data`, no campo `arquivo`. Arquivos privados (anexos e fotos de avaliação) saem por link temporário, que vale 1 hora.
- **Valores em dinheiro:** strings com duas casas decimais (`"219.90"`), para não perder centavos.
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

Os testes ficam em `tests/` e não precisam de banco, internet nem `.env`: o banco e o Supabase são substituídos por versões falsas (`tests/conftest.py` e `tests/apoio.py`). O pytest vem do `requirements-dev.txt`; os testes da migração da Vulto precisam também do `migracao_vulto/requirements.txt` (pandas) e, sem ele, são pulados.

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
- **Visão Geral:** os números e gráficos vêm de `GET /visao-geral/resumo`, que só devolve as seções que a conta pode ver:
  - `vendas_por_dia` (14 dias por canal da venda, pelo dia do pagamento, sem cancelados e sem o frete, menos os estornos no dia em que saíram: a mesma regra do faturamento da Vulto) e `mais_vendidas` (top 5 em 7 dias): permissões de venda ou Admin;
  - `chamados` (abertos, em andamento, concluídos em 7 dias e por dia): `atender_chamado` ou Admin;
  - `pontos` (o que cada marca deve em pontos hoje, o mesmo de `GET /fidelidade/passivo`): `gerenciar_fidelidade` ou Admin;
  - `rede_agora` (cobertura em dias, ruptura online, vendas de 14 dias com variação, ticket médio, `por_marca` com Casa Lorenzi × Vulto lado a lado, mediana da primeira resposta, avaliações e retiradas perto de vencer) e `por_unidade`: só Admin. `rede_agora` ignora o filtro de unidade.

  As vendas vêm por canal da venda (P3): `loja` e `site` sempre, e `app` só quando o período tem venda pelo app, que existe só no histórico da Vulto. Filtros opcionais: `marca=lorenzi|vulto` vale para todas as seções (o chamado é da marca pela unidade, pelo pedido ou pela peça ligados a ele, e o migrado do atendimento da Vulto é da Vulto; o mesmo filtro da fila do Atendimento); `referencia=AAAA-MM-DD` faz as janelas de 7, 14, 30 e 90 dias terminarem nesse dia, por exemplo `2026-09-30` para ver o histórico da Vulto, enquanto saldo, ruptura, chamados abertos, retiradas e pontos continuam sendo de agora. Os dias são no horário de Brasília. Os blocos de pendências continuam sendo o `total` de uma lista com filtro: `GET /atendimento/chamados?sem_responsavel=true`, `?meus=true&status=em_andamento` e `?com_mensagem_nova=true`; `GET /estoque?abaixo_minimo=true`; `GET /transferencias?status=enviada` e `?status=solicitada` com `id_unidade` (o frontend separa as que chegam das que saem); `GET /vendas/pedidos?status=pago` e `?pronto_ha_mais_de_dias=5`; `GET /moderacao/denuncias`.
- **Os 3 números da Vulto:** o faturamento da plataforma vem de `GET /visao-geral/faturamento?de=2025-01-01&ate=2026-09-30&marca=vulto`, uma linha por mês, unidade e canal (site e app saem do CD Cajamar); o estoque, de `GET /estoque/historico?em=2026-09-30` e `GET /estoque/em-transito`; o passivo, de `GET /fidelidade/passivo?em=2026-09-30`. Os três com `2026-09-30` dão a posição no fim de 30/09, a data-base de 01/10/2026. Os valores e como chegamos neles estão no [numeros.md](migracao_vulto/numeros.md).
- **Vendas de hoje (Caixa):** `GET /vendas/pedidos?de=<hoje>&ate=<hoje>&id_unidade=<loja>`, com a data no formato `AAAA-MM-DD`.
- **Compra online:** `POST /carrinho` mostra frete e lojas; `POST /compras` reserva por 15 minutos e cria a compra com um pedido por marca; `POST /compras/{id}/pagamentos` cria a cobrança única e `POST /compras/{id}/simular` faz o papel do gateway. O checkout de um pedido só (`POST /pedidos`, `POST /pedidos/{id}/pagamentos` e `POST /pagamentos/{id}/simular`) continua. Reserva vencida cancela a compra e os pedidos sozinha.
- **Compras da loja:** depois do `POST /clientes`, se `compras_ligadas` for maior que zero, as compras feitas em lojas com aquele CPF já aparecem em `GET /pedidos`.
- **CORS:** o navegador só deixa o frontend chamar a API se o endereço dele estiver em `CORS_ORIGINS`, sem `/` no final. Para os previews da Vercel, use `CORS_ORIGIN_REGEX`, por exemplo `https://<projeto>-.*\.vercel\.app`.

## Estado atual

**Implementado:**

- contas: cadastro de cliente (com as compras da loja ligadas pelo CPF na nota), login por e-mail (Supabase) ou CPF, perfil com permissões efetivas, correção de e-mail e CPF;
- gestão: contas internas, modelos de acesso, exceções de permissão e unidades;
- catálogo: categorias, produtos, variantes, fotos e histórico de preço, com vitrine pública, e os slides do carrossel da página inicial;
- estoque: saldo por unidade e canal, movimentações, realocação, estoque mínimo, histórico em qualquer data, peças em trânsito, gráfico de evolução e conferência de divergências;
- transferências com as etapas solicitada → enviada → recebida e cancelamento;
- vendas: carrinho com frete, checkout com reserva de 15 minutos, pagamento por gateway simulado, venda física com CPF na nota opcional, preparo, envio, retirada, entrega, cancelamento com estorno, reivindicação de compra e endereços salvos;
- compra do site: um carrinho e uma cobrança para a Casa Lorenzi e a Vulto, com um pedido por marca, frete único repartido entre os pedidos e pontos de cada marca no pedido dela (ADR 0017);
- fidelidade: regras por marca com data, extrato que nunca muda, saldo e categoria por cliente e marca, pontos com o valor da época e vencimento, crédito e estorno automáticos pelo banco, resgate no checkout e no caixa e passivo em qualquer data;
- atendimento: chamados do cliente, fila da equipe, assumir, prioridade, mensagens internas, anexos, conclusão, histórico, troca, devolução e estorno;
- balcão: troca e devolução na loja sem chamado, para pedidos com ou sem conta, busca do pedido por código, número ou CPF e consulta de peça só de leitura;
- avaliações: nota, texto e fotos, edição por 7 dias, voto útil, denúncia e moderação;
- Visão Geral: resumo com vendas por dia, mais vendidas, chamados, números da rede e linha por unidade, conforme as permissões da conta;
- migração da Vulto (plataforma): marca na unidade e no produto, permissões de marca por pessoa da equipe, EAN na variante, cliente sem conta, origem da migração, canal de venda, desconto e pagamento migrado no pedido, atendimento externo e categoria pagamento no chamado;
- migração da Vulto (dados): carga definitiva feita em 21/10/2026, com os três números conferidos no banco ([`migracao_vulto/`](migracao_vulto/README.md));
- banco com 36 tabelas, triggers, RLS, usuário restrito e prazos automáticos pelo pg_cron; testes automatizados com CI.

**Fora do escopo desta entrega:** gateway de pagamento real (ADR 0011) e as evoluções da seção 9 do [case](docs/case/Casa_Lorenzi_Case_Completo.md).

## Documentação do projeto

| Documento | Conteúdo |
|---|---|
| [docs/case/Casa_Lorenzi_Case_Completo.md](docs/case/Casa_Lorenzi_Case_Completo.md) | referência do case: contexto, regras de negócio, permissões e modelo de dados |
| [CONTEXT.md](CONTEXT.md) | glossário dos termos do domínio |
| [docs/adr/](docs/adr/) | decisões de arquitetura difíceis de reverter (ADRs 0001 a 0017) |
| [ALEMBIC_GUIDE.md](ALEMBIC_GUIDE.md) | como criar e aplicar migrations |
| [migracao_vulto/README.md](migracao_vulto/README.md) | migração da Vulto: etapas, como rodar e onde cada decisão entra no código |
| [migracao_vulto/registro_decisoes.md](migracao_vulto/registro_decisoes.md) | problemas encontrados nos dados da Vulto, decisões tomadas e impacto nos números |
| [migracao_vulto/numeros.md](migracao_vulto/numeros.md) | os três números da Vulto (faturamento, estoque e passivo de pontos), como chegamos neles e a margem de erro |
| [migracao_vulto/validacao.md](migracao_vulto/validacao.md) | o que entrou no banco e por que cada linha ficou de fora, fonte por fonte |
| [migracao_vulto/mapeamento.md](migracao_vulto/mapeamento.md) | onde cada dado da Vulto entra no banco da Casa Lorenzi |

## Como contribuir

1. Atualize a `main` (`git pull`) e crie uma branch com nome curto em kebab-case, sem prefixo (ex.: `adiciona-avaliacoes`).
2. Siga as camadas: rota em `routes/`, regra em `use_cases/`, acesso ao banco só em `repositories/`. O `commit` fica no use case (veja **Transações** em [Banco de dados](#banco-de-dados)).
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
| 401 `CPF ou senha incorretos` | CPF sem conta ou senha errada (a resposta é a mesma de propósito); a pessoa pode criar a conta com esse CPF |
| 401 `Token ausente` / `Token inválido` | header `Authorization` não enviado, token expirado ou de outro projeto |
| 422 `A reserva de 15 minutos venceu` | o cliente demorou para pagar; o pedido é cancelado e é preciso fazer outro |
| 503 `Não foi possível guardar o arquivo` | Storage fora do ar ou buckets não criados (rode `alembic upgrade head`) |
| `Form data requires "python-multipart" to be installed` | dependências desatualizadas: rode `pip install -r requirements-dev.txt` |
