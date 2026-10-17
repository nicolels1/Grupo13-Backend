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

Na raiz do repositório, com o venv ativo:

```bash
pip install -r scripts/migracao_vulto/requirements.txt
python -m scripts.migracao_vulto.padronizar --origem ../vulto-backend/data --destino staging_vulto
```

`staging_vulto/` tem CPF, e-mail e telefone de clientes: está no `.gitignore` e não deve ser compartilhada.

## Arquivos

- `normalizadores.py`: funções puras de padronização (uma por tipo de dado). Valor que não segue formato conhecido levanta `ValorInvalido` e vira pendência; nada é corrigido no chute.
- `padronizar.py`: lê cada fonte, aplica os normalizadores e grava um CSV por tabela/aba, mais `pendencias.csv`.
- Toda linha de saída tem `linha_origem` (`vendas#272`, `Clientes!15`, `csv!40`), que deve atravessar todas as etapas até o banco da Lorenzi.

## Resultado da etapa 1 (execução de 16/10/2026)

| Arquivo gerado | Linhas |
|---|---|
| `banco_vendas.csv` / `banco_itens_venda.csv` | 18.598 / 31.110 |
| `banco_compras.csv` / `banco_pontos_mov.csv` | 14.948 / 12.127 |
| `banco_entradas_estoque.csv` / `banco_estoque.csv` | 17.331 / 7.130 |
| `banco_lojas.csv` / `banco_funcionarios.csv` (sem a coluna de senha) | 7 / 24 |
| `clientes_clientes.csv` / `clientes_antigos.csv` / `clientes_resgates.csv` | 2.535 / 300 / 596 |
| `catalogo_produtos.csv` (5 abas juntas) | 1.175 |
| `contagem_set26.csv` (5 abas juntas) | 2.945 |
| `batel_vendas_manuais.csv` | 76 |
| `atendimentos.csv` (17 linhas duplicadas idênticas removidas) | 1.647 |
| `pendencias.csv` | 1.538 |

Pendências por motivo:

| Fonte | Coluna | Motivo | Qtd |
|---|---|---|---|
| vulto.db | `pontos_mov.cpf` | CPF inválido (inclui `00000000000` e telefones sem DDD) | 241 |
| vulto.db | `cliente_ref` | telefone sem DDD (`90372-6664`) | 209 |
| vulto.db | `cliente_ref` | CPF inválido | 83 |
| vulto.db | `cliente_ref` | formato desconhecido (é CNPJ, pedido corporativo) | 2 |
| clientes | `Tel` | telefone sem DDD | 425 |
| clientes | `Data Cadastro` | só mês/ano (`ago/26`) | 206 |
| clientes | `cpf` | CPF inválido | 28 |
| clientes | `pontos usados` | `1.500` com ponto de milhar | 9 |
| clientes | `E-mail` | `nao tem` | 3 |
| atendimento | `pedido` | `#1094`, `pedido 1146` | 154 |
| atendimento | `contato` | telefone sem DDD | 124 |
| catálogo | `custo` | `ver NF` | 35 |
| catálogo | `preco`, `custo`, `ean` | cabeçalho repetido na linha `Verão 26!275` | 3 |
| contagem | `quantidade` | `2 (1 no estoque + 1 vitrine)` | 16 |

## O que falta padronizar

### Automáticas (sem conflito; entram na etapa 1)

Encontradas na primeira execução; ainda não implementadas.

| Variável | Problema | Regra |
|---|---|---|
| `Clientes.Data Cadastro` | 206 datas só com mês/ano (`ago/26`) | Usar `data_ou_mes` (já existe, só a aba Resgates usa) e marcar `data_so_mes` |
| `Clientes.E-mail` | `nao tem` | Tratar como vazio |
| `atendimento.pedido` | `#1094`, `pedido 1146` (154) | Tirar `#` e a palavra "pedido" antes de classificar |
| `vendas.cliente_ref`, `compras.cliente_ref` | CNPJ (`58.535.939/0001-30`) | Novo tipo `cnpj`, com validação do dígito verificador |
| `contagem.quantidade` | `2 (1 no estoque + 1 vitrine)` (16) | Usar o número antes do parêntese (é o total escrito pela loja) |
| catálogo, aba `Verão 26` | Cabeçalho repetido na linha 275 entra como produto | Descartar linha cujo SKU é o próprio nome da coluna |
| catálogo `custo` | `ver NF` (35) | Tratar como vazio (custo desconhecido) |
| catálogo | Descontinuado é só formatação (texto tachado ou vermelho) | Ler a fonte da célula com openpyxl e gravar `descontinuado` |

### Padrão assumido (barato de errar, fácil de desfazer; entram na etapa 2 como de-para)

| Variável | Problema | Regra proposta |
|---|---|---|
| `vendas.canal`, `compras.canal` | 13 grafias, não batem com a loja | Ignorar e derivar de `loja_id` (1–4 loja física, 5 site, 6 app) |
| `vendas.status` (valores claros) | `PAGO/pago/Pago/aprovado`, `cancelado/CANCELADA` | De-para para `pago` / `cancelado` (os ambíguos estão nas decisões) |
| SKU (`estoque`, `itens_venda`, `entradas_estoque`, contagem, catálogo) | 4 formatos (`CAM004BM`, `vlt cal 001 preto 38`...) | Converter para `VLT-CAT-NNN-COR-TAM` e conferir contra o catálogo; o que não bater vira pendência |
| `entradas_estoque.fornecedor` | `Serra Azul` ×4, `Rio Claro` ×3 | De-para para um nome |
| `Clientes.Pontos` | `2.203`, `1200 pts`, `9,9k` | Ponto é milhar, tira `pts`, `k` = ×1000 (o `k` arredonda: entra na margem de erro do passivo) |
| `Resgates.pontos usados` | `1.500` | Ponto é milhar |
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
- CPF inválido (28 no cadastro, 324 nas vendas e pontos): não dá para corrigir; decidir se o cliente entra sem CPF.
- Telefone sem DDD (425 no cadastro, 209 nas vendas, 124 no atendimento): não dá para recuperar o DDD (a loja de cadastro sugere a cidade, mas é chute).
- Valor do ponto: R$ 0,10 (config) ou R$ 0,05 (regra desde mar/2025); o passivo dobra conforme a escolha.
- Expiração de 12 meses (18 para Ouro) e bônus Ouro nunca implementados: aplicar retroativamente?
- Pontos creditados em vendas canceladas (367) ou aguardando pagamento (248) e créditos do bug do app.
- Resgates anotados só na planilha (596) que não estão no banco.
- Clientes antigos (300, 170 também na aba nova): risco de contar pontos duas vezes.
- Cliente sem e-mail: a Lorenzi exige e-mail no `USUARIO`.

## Problema: celular que passa por CPF válido

O `cliente_ref` da Vulto guarda CPF, e-mail ou telefone no mesmo campo, e muitas vezes só com os dígitos. Um celular tem 11 dígitos, igual ao CPF. Hoje o `contato()` decide assim: com máscara, a máscara decide; só com dígitos, vira CPF se o dígito verificador fechar e telefone se não fechar.

O risco: cerca de 1 em cada 100 celulares passa no dígito verificador por coincidência e seria classificado como CPF.

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
