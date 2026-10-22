# Os três números da Vulto

Os três números que a Entrega 3 pede para a diretoria, calculados pelo [`numeros.py`](numeros.py) a partir do staging (etapa 1), com as regras do [registro de decisões](registro_decisoes.md). Cada linha abaixo cita a decisão que a explica.

Para gerar de novo, na raiz do repositório, com o venv ativo e o staging já gerado:

```bash
python -m migracao_vulto.numeros --origem staging_vulto --destino numeros_vulto
```

A pasta `numeros_vulto/` recebe os CSVs com o detalhe (por mês, unidade, canal e produto). Ela só tem agregados, sem dado de cliente, mas é gerada e fica fora do git.

## 1. Faturamento da Vulto, jan/2025 a set/2026

**R$ 6.438.443,35** no período, contra **R$ 94.580.496,26** no relatório do sistema da Vulto.

O que conta: venda paga, sem frete (V7), no mês do pedido pelo horário de Brasília (V16), menos as devoluções no mês em que aconteceram (V6). Não contam: cancelada (V19), aguardando pagamento (V3), estornada ou com chargeback (V4), cópia do pedido (V5), troca (V6) e teste (V8). A Batel de 02 a 22/03/2026 vem da planilha manual (V9). Loja física por loja; site e app vendem do CD Cajamar (V12), separados pelo código do pedido (P3).

| Mês | Pinheiros | Eldorado | Savassi | Batel | Site | App | **Total** | Frete (à parte) | Relatório da Vulto | Diferença |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 01/2025 | 57.852,45 | 59.135,31 | 37.302,99 | 34.590,56 | 65.042,29 | 31.793,49 | **285.717,09** | 2.657,40 | 301.127,74 | -15.410,65 |
| 02/2025 | 48.363,08 | 28.558,73 | 28.108,27 | 19.684,36 | 36.142,55 | 19.564,30 | **180.421,29** | 1.771,40 | 199.574,13 | -19.152,84 |
| 03/2025 | 43.795,40 | 44.067,87 | 27.134,07 | 23.626,32 | 46.774,12 | 28.297,52 | **213.695,30** | 1.482,60 | 228.707,87 | -15.012,57 |
| 04/2025 | 59.261,76 | 54.235,31 | 31.870,07 | 29.495,98 | 65.393,76 | 27.430,20 | **267.687,08** | 2.114,90 | 283.723,09 | -16.036,01 |
| 05/2025 | 69.221,45 | 56.561,15 | 52.559,79 | 48.363,17 | 74.910,99 | 34.380,21 | **335.996,76** | 2.224,10 | 358.777,20 | -22.780,44 |
| 06/2025 | 53.864,30 | 52.454,78 | 50.966,34 | 36.037,76 | 81.262,81 | 45.376,20 | **319.962,19** | 2.342,90 | 346.155,28 | -26.193,09 |
| 07/2025 | 56.130,92 | 53.189,43 | 37.688,06 | 31.328,15 | 60.314,79 | 35.669,31 | **274.320,66** | 2.622,00 | 297.104,08 | -22.783,42 |
| 08/2025 | 50.807,91 | 43.331,82 | 32.315,97 | 36.827,26 | 75.367,67 | 35.230,22 | **273.880,85** | 2.045,70 | 3.011.828,07 | -2.737.947,22 |
| 09/2025 | 42.254,05 | 46.195,23 | 33.654,19 | 31.540,91 | 67.238,43 | 46.391,56 | **267.274,37** | 2.094,20 | 5.175.018,59 | -4.907.744,22 |
| 10/2025 | 52.322,77 | 43.708,35 | 39.083,42 | 32.512,18 | 64.223,53 | 40.471,14 | **272.321,39** | 2.408,30 | 4.728.005,61 | -4.455.684,22 |
| 11/2025 | 75.797,30 | 82.349,74 | 50.211,06 | 52.591,05 | 111.636,47 | 73.269,46 | **445.855,08** | 4.990,80 | 8.577.759,78 | -8.131.904,70 |
| 12/2025 | 78.317,37 | 81.222,00 | 59.042,06 | 57.306,71 | 112.407,24 | 76.552,84 | **464.848,22** | 4.254,60 | 9.519.401,41 | -9.054.553,19 |
| 01/2026 | 49.904,59 | 48.323,93 | 43.356,11 | 32.669,35 | 71.577,09 | 54.266,28 | **300.097,35** | 3.885,40 | 6.693.621,85 | -6.393.524,50 |
| 02/2026 | 40.623,69 | 39.918,04 | 29.670,73 | 32.090,35 | 56.423,10 | 32.924,09 | **231.650,00** | 1.890,60 | 4.247.748,81 | -4.016.098,81 |
| 03/2026 | 36.471,86 | 41.015,55 | 34.992,74 | 37.754,53 | 81.739,10 | 52.035,57 | **284.009,35** | 2.557,10 | 6.444.567,02 | -6.160.557,67 |
| 04/2026 | 56.099,34 | 39.488,69 | 28.063,47 | 22.393,86 | 75.669,23 | 37.938,49 | **259.653,08** | 2.572,10 | 4.851.407,08 | -4.591.754,00 |
| 05/2026 | 82.172,67 | 72.263,06 | 49.904,74 | 37.902,97 | 87.653,94 | 73.279,56 | **403.176,94** | 2.821,70 | 8.963.747,72 | -8.560.570,78 |
| 06/2026 | 70.945,28 | 57.790,66 | 54.909,03 | 46.337,68 | 90.237,94 | 66.348,78 | **386.569,37** | 2.910,70 | 8.499.260,04 | -8.112.690,67 |
| 07/2026 | 55.119,87 | 47.057,67 | 38.484,83 | 43.399,98 | 83.007,54 | 65.390,69 | **332.460,58** | 3.149,20 | 8.225.979,22 | -7.893.518,64 |
| 08/2026 | 74.502,17 | 48.374,43 | 45.581,52 | 31.227,49 | 74.484,23 | 56.072,13 | **330.241,97** | 1.930,50 | 6.864.526,86 | -6.534.284,89 |
| 09/2026 | 46.226,89 | 51.985,15 | 35.407,25 | 42.013,16 | 75.773,96 | 57.198,02 | **308.604,43** | 2.522,50 | 6.762.454,81 | -6.453.850,38 |
| **Total** | **1.200.055,12** | **1.091.226,90** | **840.306,71** | **759.693,78** | **1.557.280,78** | **989.880,06** | **6.438.443,35** | **55.248,70** | **94.580.496,26** | **-88.142.052,91** |

Loja física soma R$ 3.891.282,51 e online (CD Cajamar) R$ 2.547.160,84. O relatório da Vulto põe o mês pelo dia em UTC; o nosso, pelo horário de Brasília.

### Do relatório da Vulto ao nosso número

O relatório (`/relatorio/faturamento`) soma o `total` de toda venda do período, de qualquer status, com o frete dentro, sem corrigir os centavos e agrupando em UTC. Cada real de diferença tem uma decisão:

| Passo | R$ |
|---|---:|
| Relatório do sistema da Vulto | 94.580.496,26 |
| V1 pedidos do app gravados em centavos (desde ago/2025) | −87.510.767,85 |
| V19 vendas canceladas, que o relatório soma | −221.016,61 |
| V3 pedidos aguardando pagamento que nunca foram pagos | −109.445,94 |
| V8 vendas de teste (inclui um pedido de R$ 99.999,00) | −104.134,01 |
| V6 trocas lançadas como venda nova | −74.447,20 |
| V7 frete, que fica à parte | −55.248,70 |
| V4 estornos e chargebacks | −55.247,60 |
| V5 pedidos gravados duas vezes pelo app | −29.084,27 |
| V9 Batel: planilha manual no lugar dos relançamentos | +16.809,57 |
| V16 vendas de 30/09 à noite, que o relatório põe em outubro | +529,70 |
| **Nosso faturamento** | **6.438.443,35** |

De jan a jul/2025, antes dos centavos, a diferença fica entre R$ 15 mil e R$ 26 mil por mês (canceladas, frete, trocas e estornos). Em ago/2025 o app passa a gravar em centavos, e o relatório salta para R$ 3 a 9 milhões por mês.

### Margem de erro

- R$ 2.942,53: 11 pedidos aguardando cujo cliente diz no atendimento que pagou (V3).
- Menos de R$ 1 mil: até 9 trocas que podem ter tido diferença de preço (V6).
- A V1 não tem margem: nenhum pedido do app ficou ambíguo entre reais e centavos.
- O frete (R$ 55.248,70) fica fora do número; se a diretoria decidir que é receita (V7), soma-se a ele.

**Margem total:** de −R$ 1.000,00 a +R$ 3.942,53, menos de 0,1% do faturamento. A V3 só aumenta o número e a V6 vai para os dois lados; o frete não entra, porque é uma escolha da diretoria, não um erro.

## 2. Estoque da Vulto em 01/10/2026

**15.233 peças**: 15.200 nas unidades e 33 em trânsito do CD para a Pinheiros (E13).

| Unidade | Peças | Base |
|---|---:|---|
| CD Cajamar (online) | 6.552 | WMS de 28/09 (E1, E8), menos as vendas online e as saídas de 29 e 30/09 |
| Vulto Pinheiros | 2.387 (2.300 loja física, 87 online) | contagem de 20/09; 87 peças vão para o online pelo ship from store (E7) |
| Vulto Shopping Eldorado | 2.282 | contagem sem data, considerada de 20/09 (E5) |
| Vulto Savassi | 2.119 | contagem por modelo, distribuída pelas variantes (E6) |
| Vulto Batel | 1.860 | contagem sem data, considerada de 20/09 (E5) |
| Em trânsito CD → Pinheiros | 33 | 31 envios de 21/09, um dia depois da contagem, sem recebimento (E13) |
| **Total** | **15.233** | |

A posição de cada peça é a contada mais os movimentos depois da contagem até 30/09: vendas, devoluções, recebimentos e transferências. O sistema da Vulto dizia 28.136 peças só no CD (o WMS tem 6.601) e tinha 1.156 saldos negativos. Ele não entra: as lojas valem pela contagem, o CD pelo WMS (E1), e o estoque espelhado do site e do app não é estoque (E3). O detalhe por produto está em `numeros_vulto/estoque_por_produto.csv`.

### O que conseguimos reconstruir do histórico

O histórico vai de 01/01/2025 a 30/09/2026, dia a dia, em todas as unidades (E2). Ele parte da contagem e desfaz para trás as vendas, as entradas e as transferências (E4). Com isso, a pergunta "quanto tinha na loja X há duas semanas" tem resposta em qualquer dia. Em 17/09/2026 eram 15.690 peças: CD 6.851, Pinheiros 2.453, Eldorado 2.353, Savassi 2.131 e Batel 1.902.

O que não conseguimos reconstruir:

- **Saídas sem registro:** quando, voltando no tempo, o saldo ficaria negativo, entrou mais do que ficou. Entram 718 ajustes, 2.028 peças em 21 meses, contra 21.139 peças de fornecedor e 31.379 vendidas. No CD são 1.511, concentradas em set–out/2025 e mar–abr/2026, os lançamentos de coleção; nas lojas, de 109 a 166 cada.
- **Entradas sem registro:** não aparecem como ajuste. Viram saldo maior no começo: 27.458 peças em 01/01/2025 (CD 19.538).
- **Transferências:** o CD anotou só parte das saídas. Das transferências, 3.299 têm os dois lados, 2.213 só o envio e 7.536 só o recebimento da loja, que então dá a saída do CD (E4).
- **Trocas:** a Vulto registra a peça que sai, não a que volta (V6).

### Margem de erro (em peças)

| Unidade | Motivo | Peças |
|---|---|---:|
| Batel | 82 linhas da contagem marcadas "faltou contar provador" ou "recontar" (E11) | 222 |
| Eldorado | 29 SKUs com saldo no sistema que não aparecem na contagem (E12) | 53 |
| Eldorado | contagem em 30/09 em vez de 20/09 (E5) | +36 |
| Batel | contagem em 13/09 ou 30/09 (E5) | −31 a +17 |
| Savassi | contagem em 30/09 (E5) | −8 |
| todas | peças vendidas depois da contagem que a contagem não viu (a posição negativa vira zero) | 25 |

**Margem total:** até 353 peças, para mais ou para menos (2,3% do estoque), somando o maior lado de cada motivo: 222 (E11) + 53 (E12) + 53 (data da contagem, E5: de −39, Batel e Savassi, a +53, Eldorado e Batel) + 25.

Por produto, a Savassi tem a maior margem: o total de cada modelo está certo, mas a divisão por cor e tamanho segue o sistema (E6).

## 3. Passivo de pontos do Vulto+ em 01/10/2026

**9.436.136 pontos ≈ R$ 652.718,10**, de 2.710 clientes com saldo.

| Parcela | Pontos | Valor do ponto | R$ |
|---|---:|---:|---:|
| Ganhos até fev/2025 | 3.618.226 | R$ 0,10 | 361.822,60 |
| Ganhos desde mar/2025 | 5.817.910 | R$ 0,05 | 290.895,50 |
| **Total** | **9.436.136** | | **652.718,10** |

O valor do ponto é o da época em que ele foi ganho (K3), e o resgate gasta primeiro os pontos mais antigos (FIFO). O ponto que entra com o saldo negativo primeiro paga a dívida, como na plataforma (K5f). O sistema da Vulto vale tudo a R$ 0,10 (R$ 943.613,60); se tudo valesse a regra nova, R$ 0,05, seriam R$ 471.806,80.

De onde vêm os pontos:

| Decisão | Lançamentos | Pontos |
|---|---:|---:|
| Saldo da planilha em 01/01/2025, somando os cadastros do mesmo CPF e os ATIVOS da aba antiga (K5a, K1, K5d, K2) | 2.422 | 3.680.188 |
| Créditos do banco (C) | 11.384 | 6.951.483 |
| Resgates do banco (D) | 562 | −472.900 |
| Estornos do banco (E) | 101 | −103.618 |
| Ajustes do banco (AJ), quase todos a correção do bug do app | 61 | −1.119.176 |
| Dobro do online que o sistema não deu (K4b) | 2.920 | +1.444.367 |
| Pontos de venda que não vale e bug do app sem correção (K6) | 658 | −862.858 |
| Resgates anotados só na planilha (K7a, K7c) | 91 | −83.400 |
| Saldos negativos zerados (K5f) | 4 | +2.050 |
| **Passivo em 01/10/2026** | | **9.436.136** |

Os pontos creditados em telefone, celular sem DDD ou CPF digitado errado vão para o dono certo (J1c, J1d, K2); os 486 CPFs que só existem nos pontos entram como clientes sem conta (K8). O vencimento e o bônus Ouro valem só a partir de 01/10/2026 (K4a): se o vencimento de 12 meses tivesse sido aplicado, 4,86 milhões de pontos já teriam vencido.

### Margem de erro

| Motivo | Pontos | R$ |
|---|---:|---:|
| Pontos sem dono que podem ser dívida (1 cadastro sem CPF certo e o CPF `00000000000`, K2) | +165 | +16,50 |
| Resgates da planilha sem cliente identificado, que podem reduzir a dívida (K7a, K7c: João e Alice Nogueira) | −1.500 | −75,00 |
| Saldos abreviados ("2,7k") da planilha, para mais ou para menos (K5b) | ±4.800 | ±480,00 |

**Margem total:** de −6.300 a +4.965 pontos (−R$ 555,00 a +R$ 496,50), menos de 0,1% do passivo. Para cima, +165 e +4.800; para baixo, −1.500 e −4.800.

A margem é pequena perto do total. O que mais mexe no número é a regra, não o dado: o valor do ponto (K3) e o vencimento (K4a), acima.

## Quantos clientes a Vulto tem de verdade

A Head de CRM não sabia quantos clientes do Vulto+ são de verdade ("tem gente que aparece duas, três vezes"). O tratamento ([`tratar.py`](tratar.py), `tratar_clientes`) responde: cada pessoa entra uma vez, como cliente sem conta (P2), identificada pelo CPF e, sem CPF, pelo e-mail.

| Origem | Clientes | Como |
|---|---:|---|
| Cadastro do Vulto+ (aba `Clientes`) | 2.393 | 2.535 linhas: os cadastros do mesmo CPF viram um cliente (K1) e o CPF digitado com 1 dígito errado é corrigido (K2); 1 cadastro sem CPF certo entra pelo e-mail |
| Só na aba antiga | 120 | sem compra desde 2023; os 30 ATIVOS levam o saldo de 2023 (K5d) |
| Só nos pontos | 486 | o sistema creditava pelo CPF da compra sem exigir cadastro; entram sem nome (K8) |
| Compradores com CPF fora do Vulto+ | 72 | CPF na venda do banco ou da planilha da Batel, sem pontos nem cadastro (J1a) |
| Compradores só com e-mail | 1.247 | compra no site ou no app sem CPF; o e-mail não achou um CPF no cadastro nem no atendimento (J1a, P2) |
| **Total** | **4.318** | 3.070 com CPF e 1.248 só com e-mail |

Dos 2.393 do cadastro, 928 aceitam mensagem, 744 não aceitam e 721 não informaram (K9b); 274 têm o celular sem DDD e ficam com o número incompleto (K9a).

Margem de erro: parte dos 1.248 só com e-mail pode ser a mesma pessoa de um CPF, com outro e-mail; elas se juntam quando a pessoa criar a conta com o mesmo CPF ou e-mail (J1a).
