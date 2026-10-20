# Migração da Vulto: padronização dos dados

Scripts que preparam os dados da Vulto (banco SQLite e planilhas do `vulto-legado-back`) antes de levá-los ao banco da Casa Lorenzi. A migração acontece uma vez só; o que está aqui é reprodutível: roda de novo a partir dos arquivos originais e chega no mesmo resultado.

## Etapas

| Etapa | Pasta de saída | O que faz | Situação |
|---|---|---|---|
| 0. Original | `vulto-backend/data/` | Cópia congelada dos dados da Vulto. Nunca é alterada (o SQLite é aberto só para leitura). | — |
| 1. Padronização automática | `staging_vulto/` | Formato de data, número, CPF, e-mail, telefone, texto, encoding e cabeçalho de planilha. Nenhuma regra de negócio. | **Feita** (`padronizar.py`) |
| 2. Tratamento | `tratado_vulto/` | Padrões assumidos (de-para) e decisões de negócio, com log de cada valor alterado. | A fazer |
| 3. Carga | banco da Lorenzi | Insere os dados tratados, guardando a origem de cada registro. | A fazer |
| Os três números | `numeros_vulto/` | Faturamento, estoque e passivo de pontos, calculados do staging com as decisões do registro. Só agregados. | **Feito** (`numeros.py`, resultado em [`numeros.md`](numeros.md)) |

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
python -m migracao_vulto.numeros --origem staging_vulto --destino numeros_vulto
```

Sem `VULTO_DATA_DIR`, informe a pasta no comando: `--origem ../vulto-backend/data`.

`staging_vulto/` e `tratado_vulto/` têm CPF, e-mail e telefone de clientes: estão no `.gitignore` e não devem ser compartilhadas.

## Arquivos

- `normalizadores.py`: funções puras de padronização (uma por tipo de dado). Valor que não segue formato conhecido levanta `ValorInvalido` e vira pendência; nada é corrigido no chute.
- `padronizar.py`: lê cada fonte, aplica os normalizadores e grava um CSV por tabela/aba, mais `pendencias.csv`.
- `regras.py`: as regras das decisões do registro (SKU, situação e canal da venda, centavos do app, Batel, dono dos pontos, saldo inicial, estornos, FIFO, posição contada e histórico do estoque). Usadas pelo `numeros.py` e pelo tratamento, para que os números e a carga sigam a mesma regra.
- `de_para/`: as tabelas de tradução da Vulto para a Lorenzi, em CSV, cada linha com a decisão que a justifica. Unidades, cores e status da venda (lidas pelo `regras.py`); contas internas, vendedor e atendente (P9); método de pagamento da Batel; categoria, consentimento e loja de cadastro do Vulto+; categoria do catálogo; canal, categoria e status do atendimento. Valor que não está na tabela não é traduzido no chute. O teste `tests/test_de_para_vulto.py` confere a estrutura.
- `numeros.py`: calcula os três números da Entrega 3 (faturamento com a comparação com o relatório da Vulto, estoque em 01/10/2026 com o histórico desde jan/2025, passivo de pontos) a partir do staging, com as regras do `regras.py`. Os testes estão em `tests/test_numeros_vulto.py`.
- [`numeros.md`](numeros.md): os três números, como chegamos neles e a margem de erro de cada um.
- `notebooks/`: análise exploratória (entender as fontes e testar regras antes de irem para os scripts). Ver o [README da pasta](notebooks/README.md).
- [`mapeamento.md`](mapeamento.md): onde cada dado da Vulto entra no banco da Casa Lorenzi e o que a plataforma precisa ganhar.
- [`decisoes_em_aberto.md`](decisoes_em_aberto.md): as opções que foram discutidas em cada decisão; todas estão decididas e apontam para o registro.
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

## O que ainda falta no código

Todas as decisões estão tomadas: ver o [registro de decisões](registro_decisoes.md). O que falta é levá-las para os scripts.

### Ajustes na etapa 1 (`padronizar.py`), feitos

| Decisão | Ajuste |
|---|---|
| K7b | O ponto de milhar da coluna `pontos usados` da aba Resgates é lido como milhar (`2.000` = 2.000 pts): 19 resgates passam a vir certos, e as pendências dessa coluna somem |
| C15, C9 | A linha de título "INVERNO 26 (PROVISÓRIO - MARINA)..." da aba Verão 26 sai do catálogo, e as 177 linhas abaixo dela ficam com a coleção Inverno 26, na coluna nova `colecao` (a `aba` continua como estava) |
| — | Colunas de inteiros com algum vazio voltam a ser gravadas como inteiras: `compra_id` das vendas e dos pontos, `qtd` das entradas, `loja_id` dos funcionários, `QTD` da Batel e `reservado` da contagem saíam como `2.0` |

Os três números não mudam com esses ajustes (`numeros.py` gera os mesmos CSVs).

### Etapa 2 (`tratar.py`): de onde vem cada regra

Os padrões assumidos e as decisões de negócio que esta seção listava como pendentes estão decididos:

| Assunto | Decisões |
|---|---|
| Cliente: CPF, celular, e-mail e @ | J1a, J1b, J1c, J1d, K2, K9a |
| SKU, cor, categoria e preço | J2, C1, C6, C7, C8, C13, C17, C18, C19 |
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

Os três números já aplicam essas regras sobre o staging (`numeros.py`); a etapa 2 aplica as mesmas regras linha a linha, para a carga.

## Problema: celular que passa por CPF válido

O `cliente_ref` da Vulto guarda CPF, e-mail ou telefone no mesmo campo, e muitas vezes só com os dígitos. Um celular tem 11 dígitos, igual ao CPF, e cerca de 1 em cada 100 celulares passa no dígito verificador do CPF por coincidência. Por isso a etapa 1 **não decide**: com máscara, a máscara decide; só com dígitos, o valor sai como tipo `numero` (`cliente_tipo = numero`), e a classificação fica para a etapa 2.

O sistema da Vulto também creditava pontos para qualquer `cliente_ref` só com dígitos (`if ref.isdigit()` em `/compra` e `/venda`): quando a loja digitava o celular, os pontos iam para um "CPF" que é telefone.

### Solução adotada (J1b, J1c e J1d)

Decidir em camadas, da evidência mais forte para a mais fraca (J1b, nas 5.747 vendas com `numero`):

1. **Cadastro de clientes:** o número na coluna `cpf` da aba Clientes é CPF; na coluna `Tel`, telefone (4.155 CPF e 75 telefone).
2. **Formato:** só o dígito verificador fecha → CPF; só o formato de celular (DDD válido + 9 + 8 dígitos) → telefone (1.373 CPF e 20 telefone).
3. **Serve para os dois → CPF** (85 vendas de 29 números; 57 aparecem escritas como CPF em outra fonte e nenhuma como telefone). Ficam sem cliente as 39 que não servem para nenhum dos dois.

As mesmas camadas valem para as compras e os pontos (J1c): o crédito feito num telefone do cadastro vai para o CPF do dono do telefone. O crédito em celular digitado sem DDD (J1d: 108 créditos, 38.514 pontos) vai para o dono pelos 9 últimos dígitos, sempre de um único cliente. Nenhum ponto fica órfão.
