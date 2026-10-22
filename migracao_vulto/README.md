# Migração da Vulto

Scripts que preparam os dados da Vulto (banco SQLite e planilhas do `vulto-legado-back`), levam-nos ao banco da Casa Lorenzi e conferem o resultado. A migração acontece uma vez só (a carga definitiva foi feita em 21/10/2026); o tratamento é reprodutível: roda de novo a partir dos arquivos originais e chega no mesmo resultado.

## Etapas

| Etapa | Pasta de saída | O que faz | Situação |
|---|---|---|---|
| 0. Original | `vulto-backend/data/` | Cópia congelada dos dados da Vulto. Nunca é alterada (o SQLite é aberto só para leitura). | — |
| 1. Padronização automática | `staging_vulto/` | Formato de data, número, CPF, e-mail, telefone, texto, encoding e cabeçalho de planilha. Nenhuma regra de negócio. | **Feita** (`padronizar.py`) |
| 2. Tratamento | `tratado_vulto/` | Padrões assumidos (de-para) e decisões de negócio: um CSV por tabela da plataforma, mais a origem de cada registro e cada valor alterado. | **Feito** (`tratar.py`: unidades, contas internas, catálogo, clientes, vendas, estoque, fidelidade e atendimento) |
| 3. Carga | banco da Lorenzi | Insere os dados tratados numa transação só, guardando a origem de cada registro, e confere os três números calculados pelo banco. | **Feita** em 21/10/2026 (`carregar.py --confirmar`, a partir da `main`; os três números do banco batem com o `numeros.md`) |
| Os três números | `numeros_vulto/` | Faturamento, estoque e passivo de pontos, calculados do staging com as decisões do registro. Só agregados. | **Feito** (`numeros.py`, resultado em [`numeros.md`](numeros.md)) |
| Validação | [`validacao.md`](validacao.md) | Conta, para cada fonte, as linhas que entraram e o motivo de cada uma que ficou de fora, e compara cada tabela do tratado com o banco. Só lê. | **Feita** em 21/10/2026 (`validar.py`: 0 linhas sem explicação, as 16 tabelas iguais no tratado e no banco) |

Cada etapa lê a pasta da anterior e regrava a sua do zero; regra nova entra no script e tudo roda de novo (não se cria um CSV por alteração). Os de-para da etapa 2 ficam em CSVs pequenos em `de_para/`, versionados no git, e cada valor alterado vai para um único `origem_migracao.csv`, com a decisão que o mudou.

## Como rodar

Os dados da Vulto não ficam neste repositório (têm CPF, e-mail e telefone de clientes). Clone o [vulto-legado-back](https://github.com/nadertaha06/vulto-legado-back) ao lado deste repositório, na pasta `vulto-backend`, e aponte para a pasta `data/` dele no `.env`:

```bash
git clone https://github.com/nadertaha06/vulto-legado-back ../vulto-backend
```

```
VULTO_DATA_DIR=../vulto-backend/data
```

Depois, na raiz do repositório, com o venv ativo:

```bash
pip install -r migracao_vulto/requirements.txt
python -m migracao_vulto.padronizar --destino staging_vulto
python -m migracao_vulto.tratar --origem staging_vulto --destino tratado_vulto
python -m migracao_vulto.numeros --origem staging_vulto --destino numeros_vulto
python -m migracao_vulto.carregar --origem tratado_vulto              # ensaio: carrega, confere e desfaz
python -m migracao_vulto.carregar --origem tratado_vulto --confirmar  # carga definitiva (já feita)
python -m migracao_vulto.validar --staging staging_vulto --tratado tratado_vulto --destino migracao_vulto/validacao.md
```

A carga usa a conexão direta (`DATABASE_URL_DIRECT`, a mesma das migrations) e, na definitiva, também `SUPABASE_URL` e `SUPABASE_SERVICE_ROLE_KEY`. A validação usa a mesma conexão, só para leitura.

**A carga definitiva já foi feita (21/10/2026) e não deve rodar de novo.** Com a Vulto no banco, o `carregar.py` para antes de começar, no ensaio e na definitiva. As etapas 1 e 2, os números e a validação podem rodar de novo, porque não gravam no banco.

O `tratar.py` imprime no fim os três números calculados a partir das tabelas tratadas; eles devem ser iguais aos do [`numeros.md`](numeros.md).

Sem `VULTO_DATA_DIR`, informe a pasta no comando: `--origem ../vulto-backend/data`.

`staging_vulto/` e `tratado_vulto/` têm CPF, e-mail e telefone de clientes: estão no `.gitignore` e não devem ser compartilhadas.

## Arquivos

- `normalizadores.py`: funções puras de padronização (uma por tipo de dado). Valor que não segue formato conhecido levanta `ValorInvalido` e vira pendência; nada é corrigido no chute.
- `padronizar.py`: lê cada fonte, aplica os normalizadores e grava um CSV por tabela/aba, mais `pendencias.csv`.
- `regras.py`: as regras das decisões do registro (SKU, situação e canal da venda, centavos do app, Batel, dono dos pontos, saldo inicial, estornos, FIFO, posição contada e histórico do estoque). Usadas pelo `numeros.py` e pelo `tratar.py`, para que os números e a carga sigam a mesma regra. Os testes estão em `tests/test_regras_vulto.py`.
- `tratar.py`: a etapa 2. Monta cada tabela da plataforma a partir do staging, com as regras do `regras.py` e as traduções do `de_para/`, e grava também `origem_migracao.csv` e `pendencias.csv`.
- `de_para/`: as tabelas de tradução da Vulto para a Lorenzi, em CSV, cada linha com a decisão que a justifica. Unidades, cores e status da venda (lidas pelo `regras.py`); contas internas, vendedor e atendente (P9); método de pagamento da Batel; consentimento e loja de cadastro do Vulto+ (a categoria não tem de-para: sai da regra, K4c); categoria do catálogo; canal, categoria e status do atendimento. Valor que não está na tabela não é traduzido no chute. O teste `tests/test_de_para_vulto.py` confere a estrutura.
- `carregar.py`: a etapa 3. Troca as chaves naturais pelos ids, grava tudo com COPY numa transação e confere os três números pelas consultas da plataforma. Os testes estão em `tests/test_carregar_vulto.py`.
- `validar.py`: a validação. Lê o staging, o tratado e o banco (numa transação só de leitura) e grava o [`validacao.md`](validacao.md): as linhas de cada fonte da Vulto, quantas entraram, o motivo e a decisão de cada uma que ficou de fora, e a contagem de cada tabela no tratado e no banco.
- `numeros.py`: calcula os três números da Entrega 3 (faturamento com a comparação com o relatório da Vulto, estoque em 01/10/2026 com o histórico desde jan/2025, passivo de pontos) a partir do staging, com as regras do `regras.py`.
- [`numeros.md`](numeros.md): os três números, como chegamos neles e a margem de erro de cada um.
- `notebooks/`: análise exploratória (entender as fontes e testar regras antes de irem para os scripts). Ver o [README da pasta](notebooks/README.md).
- [`mapeamento.md`](mapeamento.md): onde cada dado da Vulto entra no banco da Casa Lorenzi e o que a plataforma precisa ganhar.
- [`registro_decisoes.md`](registro_decisoes.md): cada problema encontrado nos dados ou nas regras, a decisão tomada e o impacto no número final.
- Toda linha de saída tem `linha_origem` (`vendas#272`, `Clientes!15`, `csv!40`), que deve atravessar todas as etapas até o banco da Lorenzi.

## O que a etapa 1 faz (e o que ela não decide)

Só transformações em que o formato não deixa dúvida:

- **Texto:** espaços nas pontas e repetidos; `""`, `-`, `?`, `N/A`, `nao tem`, `ver NF` viram vazio; encoding UTF-8/latin-1 do SQLite; nomes em maiúsculas/minúsculas (`ALINE BARROS` → `Aline Barros`).
- **Datas:** tudo em UTC. Com `Z` ou fuso explícito vale o fuso informado; sem fuso é horário de Brasília (o PDV das lojas grava com `datetime.now()`). Barra é sempre dia primeiro. Número de série do Excel e só mês/ano (`ago/26`, marcado em `data_cadastro_so_mes` / `data_so_mes`).
- **Valores:** `"1.299,90"`, `"R$ 129,90"` e `"589.70"` viram `Decimal`. `"1.500"` sem vírgula é ambíguo e vira pendência.
- **CPF:** só dígitos e dígito verificador. Zeros à esquerda só voltam quando a célula do Excel é número; texto com menos de 11 dígitos vira pendência (no banco costuma ser celular sem DDD).
- **CNPJ, e-mail e telefone** (`+55DDDNUMERO`; sem DDD é pendência).
- **`cliente_ref` e `contato`:** classificados só quando o formato decide (`@`, e-mail, máscara de CPF/CNPJ/telefone, DDD separado por espaço). **Número só com dígitos sai como tipo `numero`**, sem decidir se é CPF ou celular (ver a última seção).
- **Catálogo:** mapa de colunas por aba, cabeçalho repetido no meio da aba descartado, SKU em maiúsculas, tamanho, EAN como texto (o genérico `789` + zeros vira vazio), e as marcações de formatação: `descontinuado` (texto tachado ou linha inteira em vermelho) e `revisar_preco` (fundo amarelo).
- **Contagem:** mapa de colunas por aba e `"2 (1 no estoque + 1 vitrine)"` → 2 (só se as parcelas somam o total).
- **Atendimento:** linhas duplicadas idênticas removidas, tags em lista, pedido classificado pelo prefixo (`S…` site, `APP-…` app, número solto), sem `#` ou "pedido" na frente.
- A senha dos funcionários não é copiada.

## Resultado da etapa 1 (execução de 21/10/2026, com os ajustes da K7b e da C15)

| Arquivo gerado | Linhas |
|---|---|
| `banco_vendas.csv` / `banco_itens_venda.csv` | 18.598 / 31.110 |
| `banco_compras.csv` / `banco_pontos_mov.csv` | 14.948 / 12.127 |
| `banco_entradas_estoque.csv` / `banco_estoque.csv` | 17.331 / 7.130 |
| `banco_lojas.csv` / `banco_funcionarios.csv` (sem a coluna de senha) | 7 / 24 |
| `clientes_clientes.csv` / `clientes_antigos.csv` / `clientes_resgates.csv` | 2.535 / 300 / 596 |
| `catalogo_produtos.csv` (5 abas juntas, sem a linha de título da C15; 165 descontinuados, 5 para revisar preço) | 1.173 |
| `contagem_set26.csv` (5 abas juntas) | 2.945 |
| `batel_vendas_manuais.csv` | 76 |
| `atendimentos.csv` (17 linhas duplicadas idênticas removidas) | 1.647 |
| `pendencias.csv` | 1.204 |

Pendências por motivo (o que não dá para padronizar sem decidir ou que não dá para recuperar):

| Fonte | Coluna | Motivo | Qtd |
|---|---|---|---|
| vulto.db | `pontos_mov.cpf` | CPF com dígito verificador errado (inclui `00000000000`) | 134 |
| vulto.db | `pontos_mov.cpf` | CPF sem 11 dígitos: são celulares sem DDD (`947627364`) | 108 |
| vulto.db | `vendas/compras.cliente_ref` | telefone sem DDD (`90372-6664`) | 209 |
| vulto.db | `vendas/compras.cliente_ref` | CPF com máscara e dígito verificador errado | 83 |
| clientes | `Tel` | telefone sem DDD | 425 |
| clientes | `cpf` | CPF com dígito verificador errado | 28 |
| atendimento | `contato` | telefone sem DDD | 124 |
| catálogo | `descontinuado` | só a célula do SKU em vermelho, resto da linha normal | 93 |

## Como a carga (etapa 3) funciona

O `tratado_vulto/` já passa nas regras do banco (CHECK, chaves e triggers). A carga:

- **Roda numa transação só.** O ensaio (o padrão) carrega, confere e desfaz (ROLLBACK): nada fica gravado, e as sequências dos ids voltam para onde estavam. A carga definitiva (`--confirmar`) só grava (COMMIT) se os três números baterem; se não baterem, desfaz tudo. Se a Vulto já está no banco (unidade da marca `vulto`), não começa.
- **Cria as 17 contas internas da Vulto (P9) no Supabase Auth sem senha conhecida e bloqueadas**, porque a conta da plataforma é o login do Auth (ADR 0008). Elas só guardam quem vendeu, atendeu e lançou o estoque; para alguém voltar a entrar, um admin reativa a conta e a pessoa define a senha pela recuperação. No ensaio, as contas entram direto em `auth.users`, dentro da transação, e somem no ROLLBACK. Os clientes da Vulto não ganham conta: entram como cliente sem conta (P2) e se ligam à conta quando se cadastrarem com o CPF.
- **Troca a chave natural pelo id**, na ordem das ligações: unidade, conta, catálogo (as categorias que a Lorenzi já tem são achadas pelo nome), cliente sem conta, pedido, item, chamado, mensagem, pagamento, transferência, movimentação de estoque e movimento de pontos. Os ids são reservados na sequência de cada tabela antes do COPY, e chave sem registro interrompe a carga (nada é ligado no chute). O cliente é achado pelo CPF ou, de quem não tem CPF, pelo e-mail.
- **Deixa vazia a coluna que o banco preenche:** `cliente_sem_conta.criado_em` (120 clientes só da aba antiga) e `variante.ean` (49 peças, o trigger gera o código).
- **Não grava o saldo do estoque direto:** o banco recusa ([ADR 0005](../docs/adr/0005-saldo-atualizado-por-trigger.md)). O saldo nasce das movimentações, inseridas na ordem da coluna `ordem`; o `estoque.csv` serve para conferir o saldo e para gravar só o `estoque_minimo` depois. As 3.541 posições dele batem com o banco: 3.494 com peça (as que a [validação](validacao.md) compara) e 47 zeradas, que só guardam o mínimo.
- **Insere os movimentos de pontos na ordem da coluna `ordem`** (o trigger do saldo exige ordem de data) e o pagamento original antes do estorno (o estorno sai pelo mesmo método, P7).
- **Conta com os pagamentos com `origem = migracao`:** os triggers não creditam nem estornam pontos por eles; os pontos vêm só do `movimento_pontos`.
- **Grava o `origem_migracao` por último**, com o id que o banco deu a cada registro no lugar da chave natural.
- **Não carrega** o `pendencias.csv` nem o `lista_marina.csv` (peças para a Marina revisar o preço, C10 e C17): são listas para a equipe. O `pendencias.csv` não é trabalho por fazer: é o que ficou de fora por uma decisão do registro, linha a linha (19 movimentos de pontos do CPF de teste e do `00000000000`, V8 e K2; 2 resgates sem cliente, K7a e K7c).

No fim, a carga calcula os três números pelas mesmas consultas da plataforma (faturamento da visão geral, histórico e trânsito do estoque, passivo por data) e compara com os do [`numeros.md`](numeros.md). O ensaio de 21/10/2026 levou cerca de um minuto e chegou nos quatro valores: R$ 6.438.443,35; 15.233 peças; 9.436.136 pontos e R$ 652.718,10.

A carga definitiva rodou no mesmo dia, a partir da `main`, em cerca de 73 segundos: os quatro valores bateram, a transação foi gravada e as 17 contas internas ficaram bloqueadas no Supabase Auth. A [validação](validacao.md) confirma que as 16 tabelas têm no banco o mesmo número de registros do tratado e que nenhuma linha da Vulto ficou de fora sem motivo.

## Onde cada decisão entra no código

Todas as decisões estão tomadas e estão no [registro de decisões](registro_decisoes.md).

### Ajustes na etapa 1 (`padronizar.py`)

| Decisão | Ajuste |
|---|---|
| K7b | O ponto de milhar da coluna `pontos usados` da aba Resgates é lido como milhar (`2.000` = 2.000 pts): 19 resgates passam a vir certos, e as pendências dessa coluna somem |
| C15, C9 | A linha de título "INVERNO 26 (PROVISÓRIO - MARINA)..." da aba Verão 26 sai do catálogo, e as 177 linhas abaixo dela ficam com a coleção Inverno 26, na coluna nova `colecao` (a `aba` continua como estava) |
| — | Colunas de inteiros com algum vazio voltam a ser gravadas como inteiras: `compra_id` das vendas e dos pontos, `qtd` das entradas, `loja_id` dos funcionários, `QTD` da Batel e `reservado` da contagem saíam como `2.0` |

Os três números não mudam com esses ajustes (`numeros.py` gera os mesmos CSVs).

### Etapa 2 (`tratar.py`): de onde vem cada regra

| Assunto | Decisões |
|---|---|
| Cliente: CPF, celular, e-mail e @ | J1a, J1b, J1c, J1d, K2, K9a |
| SKU, cor, categoria e preço | J2, C1, C6, C7, C8, C13, C14, C17, C18, C19, C20 |
| Peça descontinuada, código reaproveitado, variante fora do catálogo | C2, C3, C4 |
| Canal, loja e status da venda | V12, P3, V2, V3, V4, V5, V13, V19 |
| Centavos do app, frete, desconto e teste | V1, V7, V18, V8 |
| Devolução e troca | V6, P7 |
| Batel em março | V9, P8 |
| Saldo, histórico e transferências do estoque | E1 a E13 |
| Pontos: saldo inicial, valor, vencimento e categoria | K1, K3, K4a, K4b, K4c, K5a a K5f |
| Pontos indevidos e resgates | K6, K7a, K8 |
| Atendimento | J3, A1 a A8, P6 |
| Autores (vendedor, SAC, transferência) | P9 |

O `numeros.py` aplica essas regras sobre o staging para chegar nos três números; o `tratar.py` aplica as mesmas regras linha a linha, para a carga.

## Problema: celular que passa por CPF válido

O `cliente_ref` da Vulto guarda CPF, e-mail ou telefone no mesmo campo, e muitas vezes só com os dígitos. Um celular tem 11 dígitos, igual ao CPF, e cerca de 1 em cada 100 celulares passa no dígito verificador do CPF por coincidência. Por isso a etapa 1 **não decide**: com máscara, a máscara decide; só com dígitos, o valor sai como tipo `numero` (`cliente_tipo = numero`), e a classificação fica para a etapa 2.

O sistema da Vulto também creditava pontos para qualquer `cliente_ref` só com dígitos (`if ref.isdigit()` em `/compra` e `/venda`): quando a loja digitava o celular, os pontos iam para um "CPF" que é telefone.

### Solução adotada (J1b, J1c e J1d)

Decidir em camadas, da evidência mais forte para a mais fraca (J1b, nas 5.747 vendas com `numero`):

1. **Cadastro de clientes:** o número na coluna `cpf` da aba Clientes é CPF; na coluna `Tel`, telefone (4.155 CPF e 75 telefone).
2. **Formato:** só o dígito verificador fecha → CPF; só o formato de celular (DDD válido + 9 + 8 dígitos) → telefone (1.373 CPF e 20 telefone).
3. **Serve para os dois → CPF** (85 vendas de 29 números; 57 aparecem escritas como CPF em outra fonte e nenhuma como telefone). Ficam sem cliente as 39 que não servem para nenhum dos dois.

As mesmas camadas valem para as compras e os pontos (J1c): o crédito feito num telefone do cadastro vai para o CPF do dono do telefone. O crédito em celular digitado sem DDD (J1d: 108 créditos, 38.514 pontos) vai para o dono pelos 9 últimos dígitos, sempre de um único cliente. Nenhum ponto fica órfão.
