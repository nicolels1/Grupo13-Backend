# Migração da Vulto: padronização dos dados

Scripts que preparam os dados da Vulto (banco SQLite e planilhas do `vulto-legado-back`) antes de levá-los ao banco da Casa Lorenzi. A migração acontece uma vez só; o que está aqui é reprodutível: roda de novo a partir dos arquivos originais e chega no mesmo resultado.

## Etapas

| Etapa | Pasta de saída | O que faz | Situação |
|---|---|---|---|
| 0. Original | `vulto-backend/data/` | Cópia congelada dos dados da Vulto. Nunca é alterada (o SQLite é aberto só para leitura). | — |
| 1. Padronização automática | `staging_vulto/` | Formato de data, número, CPF, e-mail, telefone, texto, encoding e cabeçalho de planilha. Nenhuma regra de negócio. | **Feita** (`padronizar.py`) |
| 2. Tratamento | `tratado_vulto/` | Padrões assumidos (de-para) e decisões de negócio, com log de cada valor alterado. | A fazer |
| 3. Carga | banco da Lorenzi | Insere os dados tratados, guardando a origem de cada registro. | A fazer |

Cada etapa lê a pasta da anterior e regrava a sua do zero; regra nova entra no script e tudo roda de novo (não se cria um CSV por alteração). Os de-para da etapa 2 ficarão em CSVs pequenos em `de_para/`, versionados no git, e cada valor alterado vai para um único `alteracoes.csv` com a regra (número da decisão) que o mudou.

## Como rodar

Os dados da Vulto não ficam neste repositório (têm CPF, e-mail e telefone de clientes). Clone o [vulto-legado-back](https://github.com/nadertaha06/vulto-legado-back) ao lado deste repositório e aponte para a pasta `data/` dele no `.env`:

```
VULTO_DATA_DIR=../vulto-backend/data
```

Depois, na raiz do repositório, com o venv ativo:

```bash
pip install -r migracao_vulto/requirements.txt
python -m migracao_vulto.padronizar --destino staging_vulto
```

Sem `VULTO_DATA_DIR`, informe a pasta no comando: `--origem ../vulto-backend/data`.

`staging_vulto/` e `tratado_vulto/` têm CPF, e-mail e telefone de clientes: estão no `.gitignore` e não devem ser compartilhadas.

## Arquivos

- `normalizadores.py`: funções puras de padronização (uma por tipo de dado). Valor que não segue formato conhecido levanta `ValorInvalido` e vira pendência; nada é corrigido no chute.
- `padronizar.py`: lê cada fonte, aplica os normalizadores e grava um CSV por tabela/aba, mais `pendencias.csv`.
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

## Resultado da etapa 1 (execução de 16/10/2026)

| Arquivo gerado | Linhas |
|---|---|
| `banco_vendas.csv` / `banco_itens_venda.csv` | 18.598 / 31.110 |
| `banco_compras.csv` / `banco_pontos_mov.csv` | 14.948 / 12.127 |
| `banco_entradas_estoque.csv` / `banco_estoque.csv` | 17.331 / 7.130 |
| `banco_lojas.csv` / `banco_funcionarios.csv` (sem a coluna de senha) | 7 / 24 |
| `clientes_clientes.csv` / `clientes_antigos.csv` / `clientes_resgates.csv` | 2.535 / 300 / 596 |
| `catalogo_produtos.csv` (5 abas juntas; 165 descontinuados, 5 para revisar preço) | 1.174 |
| `contagem_set26.csv` (5 abas juntas) | 2.945 |
| `batel_vendas_manuais.csv` | 76 |
| `atendimentos.csv` (17 linhas duplicadas idênticas removidas) | 1.647 |
| `pendencias.csv` | 1.213 |

Pendências por motivo (o que não dá para padronizar sem decidir ou que não dá para recuperar):

| Fonte | Coluna | Motivo | Qtd |
|---|---|---|---|
| vulto.db | `pontos_mov.cpf` | CPF com dígito verificador errado (inclui `00000000000`) | 134 |
| vulto.db | `pontos_mov.cpf` | CPF sem 11 dígitos: são celulares sem DDD (`947627364`) | 108 |
| vulto.db | `vendas/compras.cliente_ref` | telefone sem DDD (`90372-6664`) | 209 |
| vulto.db | `vendas/compras.cliente_ref` | CPF com máscara e dígito verificador errado | 83 |
| clientes | `Tel` | telefone sem DDD | 425 |
| clientes | `cpf` | CPF com dígito verificador errado | 28 |
| clientes | `pontos usados` (Resgates) | `1.500`: ponto de milhar (padrão assumido, abaixo) | 9 |
| atendimento | `contato` | telefone sem DDD | 124 |
| catálogo | `descontinuado` | só a célula do SKU em vermelho, resto da linha normal | 93 |

## O que falta padronizar

### Automáticas

Nenhuma conhecida. As oito encontradas na primeira execução foram implementadas (data só com mês/ano no cadastro, `nao tem`, `#1094`/`pedido 1146`, CNPJ, contagem com parcelas, cabeçalho repetido no catálogo, `ver NF` e marcações de formatação), além de telefone com DDD separado por espaço e pedido `APP-` com quantidade de dígitos diferente. Novas aparecem em `pendencias.csv` a cada execução.

### Padrão assumido (barato de errar, fácil de desfazer; entram na etapa 2 como de-para)

| Variável | Problema | Regra proposta |
|---|---|---|
| `vendas/compras.cliente_ref` tipo `numero` | 5.747 vendas e 8.431 compras só com dígitos: CPF ou celular? | Cruzar com o cadastro e com o formato de celular (ver a última seção) |
| `pontos_mov.cpf` com 9–10 dígitos | 108 lançamentos; celular sem DDD ou CPF sem zeros? | Cruzar com os telefones do cadastro (sem DDD) antes de completar zeros |
| `Resgates.pontos usados` | `1.500` (9) | Ponto é milhar (pontos são inteiros) |
| catálogo `descontinuado` parcial | Só o SKU em vermelho (93) | Confirmar com a Marina o que a marcação significa |
| `vendas.canal`, `compras.canal` | 13 grafias, não batem com a loja | Ignorar e derivar de `loja_id` (1–4 loja física, 5 site, 6 app) |
| `vendas.status` (valores claros) | `PAGO/pago/Pago/aprovado`, `cancelado/CANCELADA` | De-para para `pago` / `cancelado` (os ambíguos estão nas decisões) |
| SKU (`estoque`, `itens_venda`, `entradas_estoque`, contagem, catálogo) | 4 formatos (`CAM004BM`, `vlt cal 001 preto 38`...) | Converter para `VLT-CAT-NNN-COR-TAM` e conferir contra o catálogo; o que não bater vira pendência |
| `entradas_estoque.fornecedor` | `Serra Azul` ×4, `Rio Claro` ×3 | De-para para um nome |
| `Clientes.Pontos` | `2.203`, `1200 pts`, `9,9k` | Ponto é milhar, tira `pts`, `k` = ×1000 (o `k` arredonda: entra na margem de erro do passivo) |
| `Clientes.categoria` | 14 grafias, 425 vazias | De-para para bronze/prata/ouro (`vip?` fica vazio) |
| `Clientes.aceita_msg` | `1/S/sim/x` e `0/N/nao`, 827 vazios | De-para para `true`/`false`; vazio = `false` (LGPD: na dúvida, não envia) |
| `Clientes.loja_cadastro` | 18 grafias (`bh`, `CWB`, `eldorado shop`) | De-para para a unidade |
| `contagem.unidade` | Nome da loja em texto | De-para para a unidade |
| catálogo `categoria`, `cor` | `Bermudas/shorts/Bottoms > Short`, `Olive/oliva` | De-para para as categorias e cores da Lorenzi (cor pode sair do código no SKU) |
| `atendimento.canal`, `status`, `atendente` | 9, 10 e 8 grafias | De-para (`wpp` → whatsapp; `ok/resolvido/fechado` → concluido; `julia/Júlia (SAC)` → uma pessoa) |

### Decisões de negócio (custam caro ou não dá para desfazer; precisam de alguém da diretoria e entram no registro de decisões)

**Vendas e faturamento**
- Total do app gravado em centavos (~2.200 vendas, misturado com vendas em reais): como identificar cada caso (conferir contra a soma dos itens).
- Status `aguardando` (329), vazio (949), `estornado` (131), `chargeback` (38): contam como faturamento?
- Pedidos do app duplicados (`aguardando` + `PAGO`, 76 pedidos): qual linha fica.
- Devolução lançada como venda negativa (201) e troca como venda nova (328): converter em devolução/estorno da Lorenzi.
- Frete (R$ 49,6 mil): é receita (Diego) ou repasse (Paula)?
- Vendas de teste/QA em produção (37): excluir.
- Batel em março/26: datas da planilha manual ambíguas (`02/03` lido como 3 de fevereiro, `03/03` sem ano) e "as principais" relançadas no sistema (risco de duplicidade).
- Ship from store (56 vendas): baixar estoque da Pinheiros em vez do site?

**Estoque**
- Estoque negativo (1.156 SKU×loja) e lojas 5/6 (site/app) com estoque próprio duplicado do CD: qual saldo inicial vale (sistema × contagem física × WMS).
- Contagens em datas diferentes (20/09 e 28/09, outras sem data) e a Savassi só por modelo: como chegar em 01/10.
- Transferências anotadas pelos dois lados ou por um só.
- Custo unitário vazio em 95% das entradas; custo médio (financeiro) × último custo (Compras).

**Catálogo**
- Peça em duas coleções: vale a mais nova (Marina) ou o menor preço (Diego/financeiro)?
- SKU reaproveitado entre coleções.

**Clientes e Vulto+**
- CPF duplicado com dados diferentes (123 CPFs): qual cadastro fica e como somar os pontos.
- CPF com dígito verificador errado (28 no cadastro, 83 nas vendas/compras, 134 nos pontos): não dá para corrigir; decidir se o cliente entra sem CPF e o que fazer com os pontos.
- Telefone sem DDD (425 no cadastro, 209 nas vendas, 124 no atendimento): não dá para recuperar o DDD (a loja de cadastro sugere a cidade, mas é chute).
- Valor do ponto: R$ 0,10 (config) ou R$ 0,05 (regra desde mar/2025); o passivo dobra conforme a escolha.
- Expiração de 12 meses (18 para Ouro) e bônus Ouro nunca implementados: aplicar retroativamente?
- Pontos creditados em vendas canceladas (367) ou aguardando pagamento (248) e créditos do bug do app.
- Resgates anotados só na planilha (596) que não estão no banco.
- Clientes antigos (300, 170 também na aba nova): risco de contar pontos duas vezes.
- Cliente sem e-mail: a Lorenzi exige e-mail no `USUARIO`.

## Problema: celular que passa por CPF válido

O `cliente_ref` da Vulto guarda CPF, e-mail ou telefone no mesmo campo, e muitas vezes só com os dígitos. Um celular tem 11 dígitos, igual ao CPF, e cerca de 1 em cada 100 celulares passa no dígito verificador do CPF por coincidência. Por isso a etapa 1 **não decide**: com máscara, a máscara decide; só com dígitos, o valor sai como tipo `numero` (`cliente_tipo = numero`), e a classificação fica para a etapa 2.

### Tamanho do problema (dados de `vendas`)

| | Vendas |
|---|---|
| `cliente_ref` com 11 dígitos sem máscara | 5.747 |
| ... que passam como CPF válido | 5.617 |
| ... que têm formato de celular (DDD válido + 9) | 415 |
| **... que são as duas coisas (ambíguos)** | **324** (114 números distintos) |
| ambíguos encontrados na coluna `cpf` do cadastro | 235 |
| ambíguos encontrados na coluna `Tel` do cadastro | 4 |
| **ambíguos sem evidência** | **85** |

### Também afeta o passivo de pontos

O sistema da Vulto creditava pontos para qualquer `cliente_ref` só com dígitos (`if ref.isdigit()` em `/compra` e `/venda`). Quando a loja digitava o celular, os pontos iam para um "CPF" que é telefone. Em `pontos_mov`, 108 lançamentos têm 9 dígitos começando com 9 (celular sem DDD), somando **38.514 pontos**; no total, 241 lançamentos com CPF inválido somam 90.086 pontos.

### Solução proposta (ainda não implementada)

Decidir em camadas, da evidência mais forte para a mais fraca:

1. **Cadastro de clientes:** se o número está na coluna `cpf` da aba Clientes, é CPF; se está na coluna `Tel`, é telefone. Para isso, a planilha de clientes precisa ser lida antes do banco.
2. **Formato:** se não está no cadastro, comparar com o formato de celular (DDD válido + 9 + 8 dígitos):

   | Dígito verificador fecha? | Formato de celular? | Resultado |
   |---|---|---|
   | sim | não | CPF |
   | não | sim | telefone |
   | sim | sim | pendência (ambíguo) |
   | não | não | pendência |

3. **Contexto:** o mesmo número com resgate em `pontos_mov` (resgate exige CPF) ou em outra venda com nome indica CPF. O que continuar ambíguo vai para o registro de decisões com a contagem exata.

Para os pontos: lançamentos de `pontos_mov` cujo "CPF" for classificado como telefone são ligados ao cliente dono daquele telefone no cadastro, quando existir; os que não forem ligados ficam como pontos órfãos, contados à parte no passivo como margem de erro.
