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

Dependências entre os blocos: P9 (Nicole) cobre o vendedor e o autor das transferências e do cancelamento, que as decisões V14 e E4 do registro usam. Basta avisar quando decidir.

---

## Bloco 1. Decidir juntos primeiro

### J1c. A regra da J1b vale para as compras e os pontos
- **Problema:** a J1b classificou só o `vendas.cliente_ref` (5.747). As **compras** (`banco_compras`, a base do crédito de pontos) têm 8.431 `cliente_tipo = numero`, e elas geraram **8.429 créditos com 5.810.452 pontos**: quase todo o passivo passa por esses números. O "CPF" de cada crédito em `pontos_mov` é o próprio `cliente_ref` da compra.
- **Evidência:** `banco_compras` × `banco_pontos_mov` (`compra_id`) × `clientes_clientes`: 7.059 batem com um CPF do cadastro e 64 com um telefone; 7.185 têm o mesmo número de uma venda já classificada pela J1b; 538 números distintos só aparecem nas compras. 2.866 são de loja física (`pdv`), sem venda ligada pelo `compra_id`.
- **Opções:** a) aplicar as mesmas camadas da J1b a `compras.cliente_ref` e a `pontos_mov.cpf`; b) aceitar o `pontos_mov.cpf` como CPF sem classificar.
- **Resultado das camadas da J1b nas 8.431 compras:** CPF pelo cadastro 7.059 (4.699.683 pts), CPF pelo formato 1.156 (1.016.878 pts), ambíguo → CPF 110 (51.778 pts); telefone do cadastro 64 (24.860 pts); telefone fora do cadastro 14 (7.304 pts); nem CPF nem celular 28 (9.949 pts). **99,3% dos pontos continuam no CPF.**
- **Sugestão:** a). Pela J1a, o telefone serve para achar o CPF no cadastro: os 64 créditos de telefone do cadastro vão para o **CPF do dono do telefone** e continuam no passivo. Os 14 de telefone fora do cadastro ficam como pontos órfãos (margem de erro). Os 28 "nem CPF nem celular" são o mesmo caso da K2 (dígito verificador errado) e seguem a decisão dela. **Atenção:** a contagem acima só olha o `cliente_tipo = numero`; os CPFs escritos com máscara e dígito errado ficam com o tipo vazio e somam mais 24 créditos (10.989 pts). Contando os dois, a K2 cobre 50 números (21.639 pts). 3 dos números com formato de celular fora do cadastro (1.520 pts) também batem com um CPF conhecido a 1 dígito, mas seguem como telefone (órfãos).

### J1d. Pontos creditados em celular sem DDD
- **Problema:** o sistema creditava pontos para qualquer `cliente_ref` só com dígitos (`if ref.isdigit()` em `/compra` e `/venda`); com o celular digitado sem DDD, o crédito foi para um "CPF" de 9 dígitos.
- **Evidência:** `pendencias.csv` (`pontos_mov.cpf`, "CPF sem 11 dígitos"): **108 créditos, 98 números, 38.514 pontos**. 84 números (93 créditos, 32.964 pontos) batem com os 9 últimos dígitos de um telefone do cadastro, todos com um único cliente; 14 números (15 créditos, 5.550 pontos) não batem com ninguém.
- **Opções:** a) ligar ao dono do telefone (84 números) e o resto como pontos órfãos, contados à parte; b) todos como pontos órfãos; c) descartar ("pontos só com CPF").
- **Sugestão:** a). Impacto: até 38.514 pontos (R$ 1.925,70 a R$ 0,05 ou R$ 3.851,40 a R$ 0,10) entre passivo ligado e margem de erro.

### J2. Qual é o SKU oficial
- **Problema:** 4 formatos: longo `VLT-CAT-NNN-COR-TAM`, curto `CAM004BM`, com espaço e `DIVERSO`. Escrito igual, só 55% dos itens acham o catálogo; pela chave comum (categoria + número + cor + tamanho), 77%.
- **Evidência:** `02_catalogo` (ligações), `01_vendas` (24.583 itens no formato longo, 5.631 no curto, 630 com espaço).
- **Opções:**

| Opção | Ganho | Perda |
|---|---|---|
| a) Longo `VLT-CAT-NNN-COR-TAM` | É o mais usado nas vendas; o prefixo `VLT` já separa a marca | Converter o Verão 26 (curto) |
| b) Curto `CAM004BM` | É o da coleção mais nova | Minoria nas vendas; a inicial da cor é ambígua (`OLI` × `OFF`) |
| c) Formato da Lorenzi | Um padrão só no grupo | Mais conversão; código novo para peças que a operação já conhece |

- **Sugestão:** a), guardando o código original na origem da migração (P4).

### J3. Código do pedido citado no atendimento (atendimento → venda)
- **Problema:** o atendimento só acha o pedido pelo número original da venda da Vulto, e esse número aparece de vários jeitos: na coluna `pedido` ou só no texto da mensagem; `S…`, `APP-…`, `APP` sem o prefixo (`7002175`), o `id` interno da venda (loja física, que não tem código) e até com os dígitos invertidos (`874101` → `S101478`).
- **Evidência:** `05_atendimentos`, seção 5: 282 pedidos só no texto; 89 do app sem prefixo (nenhum bate com o `id` interno); dos 47 invertidos que batem, 43 com a venda até 60 dias antes (mediana de 9 dias, igual aos códigos normais).
- **Depende das vendas (Matias):** `pedido_externo` vira `codigo_venda`, e o `id` da Vulto fica guardado na origem da migração (P4). Sem isso, nenhuma destas ligações funciona.
- **Opções:**

| Opção | Ganho | Perda |
|---|---|---|
| a) Todas as regras: coluna e texto; app sem prefixo; número solto e invertido só com a venda até 60 dias antes | 928 atendimentos acham a venda | Mais regras para explicar; os 43 invertidos se apoiam só na data |
| b) Só a coluna, nos formatos conhecidos (`S…`, `APP-…`, número solto) | Simples | Perde ~370 ligações (as do texto, as do app sem prefixo e as invertidas) |
| c) a) sem os invertidos | Nenhuma ligação "criativa" | Perde 43 |

- **Sugestão:** a). Cada ligação guarda a regra que a fez (`como_achou_venda` no notebook), para mostrar na banca.

### P2. Cliente da Vulto sem login
- **Por quê:** ~2.500 clientes do Vulto+ (mais 534 CPFs que só existem nos pontos), e o nosso banco só tem cliente com conta no Supabase Auth, com e-mail e CPF obrigatórios. Pedido online e chamado exigem cliente.
- **Quem vira cliente sem conta (J1a):** quem tem CPF fora do cadastro e, sem CPF, quem tem e-mail. Nas vendas, 1.964 online são de 1.248 pessoas só com e-mail; no atendimento, 311 dos 1.104 com cliente (122 com CPF fora do cadastro e 189 só com e-mail).
- **O que a c) exige no cadastro do site:** o "CPF já cadastrado" só olha `usuario`, então o cliente sem conta não trava o cadastro. Ao criar a conta, o backend puxa do cliente sem conta com aquele CPF **ou** aquele e-mail os pedidos, os chamados e os pontos, como `ligar_pedidos_pelo_cpf` já faz com o CPF na nota (ADR 0014). Na a), o CPF já estaria em `usuario` e a pessoa não conseguiria se cadastrar nem entrar.
- **Regras do cliente sem conta:** CPF único; e-mail único; pelo menos um dos dois; guarda a camada que o identificou (J1b).
- **Sugestão futura:** juntar pelo e-mail só depois de confirmar o e-mail. Hoje o cadastro não confirma (Entrega 2), e alguém que digitasse o e-mail de outra pessoa puxaria o histórico dela; é o mesmo risco aceito no CPF na nota.

| Opção | Ganho | Perda |
|---|---|---|
| a) Criar uma conta no Auth para cada cliente | As regras atuais não mudam | Cria conta que o cliente não pediu (o ADR 0014 tirou isso do caixa pelo mesmo motivo); exige e-mail que parte não tem; CPFs só nos pontos ficam de fora |
| b) Trocar `usuario` por uma tabela `cliente` separada da conta | Modelo mais limpo | Mexe nas chaves de pedido, chamado e endereço: muito código da Entrega 2 |
| c) Tabela de clientes sem conta (CPF, nome, e-mail, telefone, consentimento, origem), ligada a pedido e chamado; quando a pessoa cria a conta pelo site, tudo passa para a conta | Segue a regra do ADR 0014 (CPF na nota sem conta); não inventa e-mail | Duas chaves opcionais em pedido e chamado; ajustar `online_exige_cliente` para aceitar o cliente sem conta |

- **Sugestão:** c). Justificativa: "seguimos a mesma regra do ADR 0014: não criamos conta que o cliente não pediu; o histórico aparece quando ele se cadastra".

### P7. Pagamento e devolução da Vulto
- **Por quê:** `pagamento.metodo` é obrigatório e a Vulto só registra o método na planilha da Batel; a devolução da Vulto é uma venda negativa, e o nosso banco não aceita.

| Opção | Ganho | Perda |
|---|---|---|
| a) Método `nao_informado` só para os migrados | Todo pedido pago tem pagamento; a Batel entra com o método real | Um valor a mais no `metodo` |
| b) Pedido migrado sem linha de pagamento | Não inventa dado | Sem pagamento não há estorno (o estorno aponta para o pagamento original) |

- **Sugestão:** a). A devolução depende da V6.

### V2. Status vazio (949 vendas das lojas físicas, R$ 317.163,75)
- **Evidência:** todas de jan–fev/2025; nenhuma venda "pago" em loja física antes de 01/03/2025, mas as canceladas já eram marcadas.
- **Opções:** a) contar como paga; b) excluir.
- **Sugestão:** a), registrada como premissa: "o PDV só passou a gravar 'pago' em mar/2025". Impacto: +R$ 317 mil no faturamento de jan–fev/2025.

### V3. `aguardando` (329) — 44 deles são a cópia de um pedido pago (V5)
- **Opções:** a) não conta no faturamento e entra como pedido cancelado; b) conta.
- **Sugestão:** a). Nunca foi pago, e o mais recente é de antes de 01/10. Falta escolher o `motivo_cancelamento`.

### V4. `estornado` (131) e `chargeback` (38)
- **Opções:** a) não conta (faturamento líquido); b) conta e mostra o estorno à parte.
- **Sugestão:** a). O CFO compara com o banco, e no banco esse dinheiro voltou. Entram como pedido pago + estorno (P7).

### V5. Pedidos do app duplicados (76)
- **Evidência:** mesmo valor, mesmos itens, menos de 90 segundos de diferença.
- **Sugestão:** manter a linha mais recente (sempre paga). Os pontos em dobro desses pedidos vão para a K6.

---

## Bloco 2. Nicole

### Plataforma

#### P1. Marca (Lorenzi × Vulto)
- **Por quê:** "as peças da Vulto convivendo com as da Lorenzi"; a diretoria quer relatórios por rede.

| Opção | Ganho | Perda |
|---|---|---|
| a) Coluna `marca` em `unidade` e `produto` | Simples; filtro direto | Migration em duas tabelas |
| b) Tabela `marca` | Aceita uma terceira marca no futuro | Mais uma tabela para duas marcas |
| c) Deduzir pelo prefixo do SKU ou pela unidade | Nada muda no banco | Frágil: o CD e o site atendem as duas marcas |

- **Sugestão:** a).

#### P3. Canal detalhado (loja, site, app)
- **Por quê:** o faturamento é pedido por canal, e `pedido.canal` só separa físico × online.

| Opção | Ganho | Perda |
|---|---|---|
| a) Coluna nova no pedido (ex.: `origem`: `loja`, `site`, `app`) | Não mexe no estoque por canal (ADR 0004) | Mais uma coluna |
| b) Ampliar `canal` para `site` e `app` | Uma coluna só | `canal` também é o canal do estoque: quebra o ADR 0004 e vários triggers |
| c) Deduzir pelo código do pedido (`S…`, `APP-…`) | Nada muda | Só funciona para os pedidos da Vulto |

- **Sugestão:** a).

#### P4. Origem da migração (rastreabilidade)
- **Por quê:** cada registro precisa dizer de onde veio (`vendas#272`, `Clientes!15`) e que regra o mudou.

| Opção | Ganho | Perda |
|---|---|---|
| a) Coluna `linha_origem` em cada tabela migrada | Aparece direto na consulta | Coluna em ~12 tabelas que só a Vulto usa |
| b) Uma tabela `origem_migracao` (tabela, id, linha de origem, valor original) | Um lugar só; não mexe nas tabelas da Entrega 2 | Uma junção para mostrar na demo |

- **Sugestão:** b).

#### P5. Fidelidade (Vulto+)
- **Por quê:** "clientes e saldo de pontos" fazem parte da união; o passivo é um dos três números.

| Opção | Ganho | Perda |
|---|---|---|
| a) Movimentos de pontos (crédito, resgate, estorno, ajuste, vencimento) com o saldo calculado, no mesmo padrão do estoque (ADR 0005); saldo da planilha como saldo inicial em 01/01/2025 e os movimentos do banco depois | Histórico completo; o passivo sai do nosso banco e cada ponto tem origem | Mais trabalho (tabelas e trigger) |
| b) Só o saldo final por cliente | Rápido | Não explica o passivo; a banca pergunta "de onde veio esse número?" |
| c) Não migrar; passivo só num relatório | Nada muda no banco | Não atende "uma plataforma só"; as planilhas continuariam em uso |

- **Sugestão:** a). As regras (valor do ponto, vencimento) ficam numa tabela de configuração, com a data em que valem.

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
- **Por quê:** a mensagem exige autor e a transferência exige solicitante; vendedor e quem lançou o estoque estão em texto livre (48 grafias de vendedor).

| Opção | Ganho | Perda |
|---|---|---|
| a) Uma conta interna inativa por funcionário (24) | Autoria real | Contas no Auth com e-mail inventado; as grafias não batem com o cadastro |
| b) Uma conta "Migração Vulto" inativa como autor, e o nome original guardado em texto (P4 ou P6) | Uma conta só; nada inventado | Não filtra por vendedor da Vulto |
| c) Descartar a autoria | Simples | Perde quem atendeu e quem vendeu |

- **Sugestão:** b).

### Clientes e Vulto+

#### K1. CPF com 2 ou mais cadastros (131)
Decidida: ver K1 no [registro de decisões](registro_decisoes.md).

#### K2. CPF com dígito verificador errado (28 no cadastro, 134 nos pontos)
Decidida: ver K2 no [registro de decisões](registro_decisoes.md).

#### K3. Valor do ponto: R$ 0,10 (sistema) × R$ 0,05 (regra desde mar/2025)
- **Sugestão:** R$ 0,05, a regra vigente, mostrando também o valor a R$ 0,10. O passivo em R$ dobra conforme a escolha.

#### K4. Vencimento (12 meses; 18 para Ouro), Ouro +50% e dobro no online
- **Opções:** a) aplicar retroativamente; b) **não aplicar no passado e passar a aplicar a partir da migração**.
- **Sugestão:** b) para o vencimento e o Ouro: o cliente nunca viu ponto vencer. Para o dobro no online gravado errado (`web`, `ecommerce`), corrigir a favor do cliente; o impacto fica a calcular.

#### K5. Saldo inicial
Decidida: ver K5a a K5f no [registro de decisões](registro_decisoes.md).

#### K6. Pontos indevidos: venda cancelada (367), aguardando (248), pedido duplicado (76), bug do app (4 clientes, 215.982)
- **Depende de:** V3, V4 e V5 (quais vendas não valem).
- **Sugestão:** estornar os créditos sem venda paga que ainda não foram estornados e os 215.982 pontos dos 4 ainda inflados.

#### K7. Resgates só na planilha (~41) e sem CPF (52)
- **Sugestão:** descontar os 41 com CPF; os 52 sem CPF ficam como margem de erro.

#### K8. CPFs só no banco de pontos (534)
- **Evidência:** começaram em 2025–2026. 24 são o CPF certo de um cadastro com dígito errado e se resolvem pela K2; os outros 510 não diferem em 1 dígito de nenhum CPF do cadastro.
- **Sugestão:** entram como clientes sem conta, com CPF e saldo, sem nome.

#### K9. Telefone sem DDD (425) e `aceita_msg` vazio (827)
- **Sugestão:** o telefone fica como está, marcado como incompleto (não inventar DDD). Consentimento vazio = não aceita (LGPD).

### Atendimento

#### A1. Instagram sem como achar o cliente pelo contato (408)
- **Evidência:** `05_atendimentos`, seção 5 (com a J1): pelo pedido, 204 ganham cliente (120 do cadastro, 30 com CPF fora dele e 54 só pelo e-mail, P2); 21 chegam a uma venda sem cliente e 3 a um comprador só com telefone; 84 são pré-venda, 19 têm nome igual (A5) e 71 ficam sem nada.
- **Sugestão:** o @ fica como contato externo (P6) em todos; o cliente vem do pedido quando houver.

#### A2. Número solto que bate com o id da venda (399, contando os do texto)
- **Evidência:** `05_atendimentos`: na coluna, em 311 dos 329 a venda é de até 60 dias antes; no texto, mais 88.
- **Sugestão:** ligar só com a venda até 60 dias antes (regra da J3); o resto fica sem pedido.

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
