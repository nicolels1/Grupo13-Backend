# Decisões em aberto da migração da Vulto

Material para a etapa 4. Cada item traz o problema, a evidência, as opções com ganho e perda e uma **sugestão** apoiada no case. **Nada aqui está decidido**: a sugestão é um ponto de partida para a discussão. Quando um item for decidido, ele sai daqui e vira uma linha no [registro de decisões](registro_decisoes.md), no formato pedido pela Entrega 3.

As evidências estão nos notebooks (`notebooks/`, seção "Anotações para o dicionário de dados") e o destino de cada dado, no [mapeamento](mapeamento.md). Os padrões assumidos (de-para de grafias, baratos de errar) estão no [README](README.md#padrão-assumido-barato-de-errar-fácil-de-desfazer-entram-na-etapa-2-como-de-para) e não se repetem aqui.

Valores em R$ só das lojas físicas: as vendas do app ainda estão misturadas entre reais e centavos (V1), então os valores do online só valem depois da correção.

## Ordem

As decisões de plataforma (P) mudam o formato em que vendas e estoque entram, e as decisões sobre vendas (V) mudam o passivo de pontos. Por isso a ordem é: primeiro o bloco 1, junto, porque ele trava os outros; depois os blocos 2 e 3, em paralelo.

| Bloco | Quem | Decisões |
|---|---|---|
| 1. Decidir juntos primeiro | Nicole e Matias | J1c, J1d, J2, J3, P2, P7, V2–V5 |
| 2. Nicole | Nicole | P1, P3–P6, P8, P9, K1–K9, A1–A6, U1 |
| 3. Matias | Matias | V18, E13, C18 |

O que cada decisão do bloco 1 destrava:

| Decisão | Destrava | Por quê |
|---|---|---|
| J1c e J1d CPF × celular nas compras e nos pontos | K1–K7, passivo | quase todo o passivo passa pelo `cliente_ref` das compras |
| J2 SKU oficial | C13, E10 e a conversão do SKU em catálogo, estoque e vendas | todo vínculo com a peça passa por ela |
| J3 código do pedido citado no atendimento | A1, A2, A6 | o atendimento só acha o pedido pelo número original da venda |
| P2 cliente sem conta | vendas online, K8, A1 | pedido online e chamado exigem cliente |
| P7 pagamento e estorno | V4 | define como estorno e devolução entram |
| V2–V5 o que conta como venda paga | K6, A2, faturamento | pontos de vendas que não valem; qual pedido sobra para o atendimento |

Dependências entre os blocos: a P9 (decidida) define a conta "Migração Vulto", autora das transferências e dos cancelamentos que a V14 e a E4 usam.

---

## Bloco 1. Decidir juntos primeiro

### J1c. A regra da J1b vale para as compras e os pontos
Decidida: ver J1c no [registro de decisões](registro_decisoes.md).

### J1d. Pontos creditados em celular sem DDD
Decidida: ver J1d no [registro de decisões](registro_decisoes.md).

### J2. Qual é o SKU oficial
Decidida: ver J2 no [registro de decisões](registro_decisoes.md).

### J3. Código do pedido citado no atendimento (atendimento → venda)
Decidida: ver J3 no [registro de decisões](registro_decisoes.md).

### P2. Cliente da Vulto sem login
Decidida: ver P2 no [registro de decisões](registro_decisoes.md).

### P7. Pagamento e devolução da Vulto
Decidida: ver P7 no [registro de decisões](registro_decisoes.md).

### V2. Status vazio (949 vendas das lojas físicas, R$ 317.163,75)
Decidida: ver V2 no [registro de decisões](registro_decisoes.md).

### V3. `aguardando` (329) — 44 deles são a cópia de um pedido pago (V5)
Decidida: ver V3 no [registro de decisões](registro_decisoes.md).

### V4. `estornado` (131) e `chargeback` (38)
Decidida: ver V4 no [registro de decisões](registro_decisoes.md).

### V5. Pedidos do app duplicados (76)
Decidida: ver V5 no [registro de decisões](registro_decisoes.md).

---

## Bloco 2. Nicole

### Plataforma

#### P1. Marca (Lorenzi × Vulto)
Decidida: ver P1 no [registro de decisões](registro_decisoes.md).

#### P3. Canal detalhado (loja, site, app)
Decidida: ver P3 no [registro de decisões](registro_decisoes.md).

#### P4. Origem da migração (rastreabilidade)
- **Por quê:** cada registro precisa dizer de onde veio (`vendas#272`, `Clientes!15`) e que regra o mudou.

| Opção | Ganho | Perda |
|---|---|---|
| a) Coluna `linha_origem` em cada tabela migrada | Aparece direto na consulta | Coluna em ~12 tabelas que só a Vulto usa |
| b) Uma tabela `origem_migracao` (tabela, id, linha de origem, valor original) | Um lugar só; não mexe nas tabelas da Entrega 2 | Uma junção para mostrar na demo |

- **Sugestão:** b).

#### P5. Fidelidade (Vulto+)
Decidida: ver P5 no [registro de decisões](registro_decisoes.md).

#### P6. Atendimento externo (WhatsApp, Instagram, e-mail)
- **Por quê:** a gerente quer os atendimentos "no nosso módulo, ligados ao cliente e ao pedido certo".

| Opção | Ganho | Perda |
|---|---|---|
| a) No chamado: `canal` e `contato_externo`, cliente opcional quando vem de fora; na mensagem: autor opcional + nome do autor externo | Os 1.633 entram (os sem cliente, com o contato externo), e a conversa vira mensagens | Ajuste em `chamado` e `mensagem` |
| b) Só o chamado, com a conversa inteira na descrição | Menos trabalho | O histórico fica num bloco só, diferente dos chamados da Lorenzi |
| c) Migrar só os que acham cliente | Nada muda no banco | Perde 529 dos 1.633 (255 de pré-venda), inclusive 74 de pós-venda que acham o pedido e 161 sem nenhum identificador |

- **Sugestão:** a). Separar as falas pelo `[Atendente]:` (1.105 conversas têm). O cliente opcional vale só para os migrados: chamado novo continua exigindo conta (A8).

#### P8. Item sem variante (`DIVERSO`, Batel)
| Opção | Ganho | Perda |
|---|---|---|
| a) Um produto e uma variante genéricos ("Item sem cadastro Vulto"), inativos e sem estoque | Nada muda no banco | Não baixa estoque desses itens |
| b) `item_pedido.id_variante` opcional + descrição | Fiel ao original | Mexe numa regra central do pedido |

- **Sugestão:** a). 266 itens `DIVERSO` + as 72 vendas da Batel.

#### P9. Funcionários da Vulto como autores
Decidida: ver P9 no [registro de decisões](registro_decisoes.md).

### Clientes e Vulto+

#### K1. CPF com 2 ou mais cadastros (131)
Decidida: ver K1 no [registro de decisões](registro_decisoes.md).

#### K2. CPF com dígito verificador errado (28 no cadastro, 134 nos pontos)
Decidida: ver K2 no [registro de decisões](registro_decisoes.md).

#### K3. Valor do ponto: R$ 0,10 (sistema) × R$ 0,05 (regra desde mar/2025)
Decidida: ver K3 no [registro de decisões](registro_decisoes.md).

#### K4. Vencimento (12 meses; 18 para Ouro), Ouro +50% e dobro no online
- **Opções:** a) aplicar retroativamente; b) **não aplicar no passado e passar a aplicar a partir da migração**.
- **Sugestão:** b) para o vencimento e o Ouro: o cliente nunca viu ponto vencer. Para o dobro no online gravado errado (`web`, `ecommerce`), corrigir a favor do cliente; o impacto fica a calcular.

#### K5. Saldo inicial
Decidida: ver K5a a K5f no [registro de decisões](registro_decisoes.md).

#### K6. Pontos indevidos: venda cancelada (367), aguardando (248), pedido duplicado (76), bug do app (4 clientes, 215.982)
Decidida: ver K6 no [registro de decisões](registro_decisoes.md).

#### K7. Resgates só na planilha (~41) e sem CPF (52)
Decidida: ver K7a e K7b no [registro de decisões](registro_decisoes.md).

#### K8. CPFs só no banco de pontos (534)
Decidida: ver K8 no [registro de decisões](registro_decisoes.md).

#### K9. Telefone sem DDD (425) e `aceita_msg` vazio (827)
Decidida: ver K9a e K9b no [registro de decisões](registro_decisoes.md).

### Atendimento

#### A1. Instagram sem como achar o cliente pelo contato (408)
- **Evidência:** `05_atendimentos`, seção 5 (com a J1): pelo pedido, 204 ganham cliente (120 do cadastro, 30 com CPF fora dele e 54 só pelo e-mail, P2); 21 chegam a uma venda sem cliente e 3 a um comprador só com telefone; 84 são pré-venda, 19 têm nome igual (A5) e 71 ficam sem nada.
- **Sugestão:** o @ fica como contato externo (P6) em todos; o cliente vem do pedido quando houver.

#### A2. Número solto que bate com o id da venda (399, contando os do texto)
Decidida: ver A2 no [registro de decisões](registro_decisoes.md).

#### A3. Spam (10) e "teste" (4)
- **Sugestão:** não migrar.

#### A4. Categoria, status e encerramento
- **Sugestão:** de-para do `assunto`/`tags` para `entrega`, `troca_devolucao`, `estorno`, `duvida` e `outros`. Concluído vira `concluido` + `resolvido`; o que estava em aberto há mais de 30 dias vira `concluido` + `sem_resposta`.

#### A5. Nome do contato igual ao de um único cliente (39)
- **Problema:** sem contato nem pedido que ache o cliente, mas o `nome_contato` (nome completo) é igual ao de um único cliente do cadastro. A J1a não prevê o nome como caminho até o CPF.
- **Opções:** a) ligar ao cliente; b) **não ligar e guardar o nome como contato externo**.
- **Sugestão:** b). Segue a J1a, e um homônimo poria a conversa de uma pessoa no histórico de outra (LGPD).

#### A6. Atendimentos sem nenhum identificador (161)
- **Evidência:** `05_atendimentos`, seção 5: nem o contato nem o pedido acham alguém. Troca/devolução 90 (28 "comprei na loja X", 26 com defeito), Vulto+ 50, entrega 7, pagamento 6, sem tag 8; 36 em aberto. O `usuario` da Lorenzi não guarda telefone nem @, então eles não se ligam sozinhos depois.
- **Opções:** a) **migrar sem cliente, com o contato externo (P6)**; b) achar a venda pela loja, pela peça e pela data; c) não migrar.
- **Sugestão:** a), e eles entram como margem de erro do atendimento. b) é chute (uma ligação errada é pior do que nenhuma); c) perde histórico de pós-venda. Os 50 de Vulto+ ("meus pontos sumiram", "ganhei 800 pontos numa compra de 300") servem de evidência para a K6.

### Unidades

#### U1. Endereço das unidades da Vulto (obrigatório, a Vulto não tem)
- **Sugestão:** pedir à Vulto ou usar o endereço público de cada loja; sem isso, a carga das unidades não passa.

---

## Bloco 3. Matias

### Vendas e faturamento

#### V18. Desconto
- **Problema:** a Lorenzi não tem campo de desconto no pedido.
- **Evidência:** `01_vendas`: 2.898 vendas com desconto; em 2.655 o total já vem com o desconto aplicado; em 243 o desconto não bate com o total.
- **Opções:** a) não migrar o valor do desconto (o total já está certo; perde-se só "quanto foi de desconto"); b) criar `pedido.valor_desconto` (guarda a informação; mais uma mudança na plataforma).
- **A conferir antes de decidir:** as 243 vendas em que o desconto não bate.

### Estoque

#### E13. Transferência em trânsito em 01/10
- **Problema:** peça que o CD enviou antes de 01/10 e a loja recebeu depois (ou nunca anotou) não está no WMS nem na contagem da loja.
- **Opções:** a) migrar como transferência `enviada` (em trânsito), que a Lorenzi aceita; b) ignorar (as peças em trânsito somem do estoque).
- **Depende da E4:** é juntando saída e entrada que aparecem as transferências sem entrada.

### Catálogo

#### C18. Categorias da Vulto: compartilhadas com a Lorenzi ou separadas
- **Problema:** o catálogo da Vulto tem 57 grafias de categoria, e a Lorenzi já tem as suas (Camisetas, Calças...).
- **Opções:** a) de-para para as categorias da Lorenzi, criando só as que faltarem (o relatório "quanto vendemos de calças no grupo" sai direto); b) categorias próprias da Vulto (separa as redes, mas duplica "Calças").
- **Observação:** a divisão entre as redes no site vem da marca (P1), não da categoria: com categorias compartilhadas, o site pode ter uma vitrine com filtro de marca ou uma seção por marca. Como o site mostra isso é decisão de tela (Nicole).
