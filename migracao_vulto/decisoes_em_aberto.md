# Decisões em aberto da migração da Vulto

Material para a etapa 4. Cada item traz o problema, a evidência, as opções com ganho e perda e uma **sugestão** apoiada no case. **Nada aqui está decidido**: a sugestão é um ponto de partida para a discussão. Quando um item for decidido, ele sai daqui e vira uma linha no [registro de decisões](registro_decisoes.md), no formato pedido pela Entrega 3.

As evidências estão nos notebooks (`notebooks/`, seção "Anotações para o dicionário de dados") e o destino de cada dado, no [mapeamento](mapeamento.md). Os padrões assumidos (de-para de grafias, baratos de errar) estão no [README](README.md#padrão-assumido-barato-de-errar-fácil-de-desfazer-entram-na-etapa-2-como-de-para) e não se repetem aqui.

Valores em R$ só das lojas físicas: as vendas do app ainda estão misturadas entre reais e centavos (V1), então os valores do online só valem depois da correção.

## Sumário

| # | Decisão | Quem puxa | Afeta |
|---|---|---|---|
| J1–J2 | Identificação do cliente; SKU oficial | Os dois | tudo |
| P1–P9 | O que a plataforma ganha | Nicole | demo e carga |
| V1–V10 | Vendas e faturamento | Matias | faturamento |
| E1–E5 | Estoque | Matias | estoque em 01/10 |
| C1–C5 | Catálogo | Matias | produtos e preço |
| K1–K9 | Clientes e Vulto+ | Nicole | passivo de pontos |
| A1–A4 | Atendimento | Nicole | módulo de atendimento |
| U1 | Endereço das unidades | Nicole | carga |

---

## Dos dois juntos

### J1. Como identificar o cliente (CPF × telefone × e-mail × @Instagram)
- **Problema:** vendas, pontos e atendimento identificam o cliente por campos diferentes, e o `cliente_ref` mistura CPF, e-mail e telefone. 5.747 vendas só com dígitos podem ser CPF ou celular.
- **Evidência:** `01_vendas` (ligações: 4.155 batem com CPF do cadastro, 75 com telefone, 1.517 não acham); README, seção "celular que passa por CPF válido" (324 ambíguos, 85 sem evidência).
- **Opções:**

| Opção | Ganho | Perda |
|---|---|---|
| a) Camadas: cadastro → formato → contexto (proposta do README) | Usa a evidência mais forte primeiro; cada ligação diz de onde veio | Mais trabalho; 85 vendas continuam ambíguas |
| b) Só dígito verificador: passou é CPF | Simples | Liga ~110 celulares a "CPFs" de pessoas que não existem |
| c) Só o que está no cadastro | Nenhuma ligação errada | Perde ~1.500 vendas que dariam para ligar |

- **Sugestão:** a). Os ambíguos ficam sem cliente e entram como margem de erro.

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

---

## Plataforma (Nicole)

### P1. Marca (Lorenzi × Vulto)
- **Por quê:** "as peças da Vulto convivendo com as da Lorenzi"; a diretoria quer relatórios por rede.

| Opção | Ganho | Perda |
|---|---|---|
| a) Coluna `marca` em `unidade` e `produto` | Simples; filtro direto | Migration em duas tabelas |
| b) Tabela `marca` | Aceita uma terceira marca no futuro | Mais uma tabela para duas marcas |
| c) Deduzir pelo prefixo do SKU ou pela unidade | Nada muda no banco | Frágil: o CD e o site atendem as duas marcas |

- **Sugestão:** a).

### P2. Cliente da Vulto sem login
- **Por quê:** ~2.500 clientes do Vulto+ (mais 534 CPFs que só existem nos pontos), e o nosso banco só tem cliente com conta no Supabase Auth, com e-mail e CPF obrigatórios. Pedido online e chamado exigem cliente.

| Opção | Ganho | Perda |
|---|---|---|
| a) Criar uma conta no Auth para cada cliente | As regras atuais não mudam | Cria conta que o cliente não pediu (o ADR 0014 tirou isso do caixa pelo mesmo motivo); exige e-mail que parte não tem; CPFs só nos pontos ficam de fora |
| b) Trocar `usuario` por uma tabela `cliente` separada da conta | Modelo mais limpo | Mexe nas chaves de pedido, chamado e endereço: muito código da Entrega 2 |
| c) Tabela de clientes sem conta (CPF, nome, e-mail, telefone, consentimento, origem), ligada a pedido e chamado; quando a pessoa cria a conta pelo site, tudo passa para a conta | Segue a regra do ADR 0014 (CPF na nota sem conta); não inventa e-mail | Duas chaves opcionais em pedido e chamado; ajustar `online_exige_cliente` para aceitar o cliente sem conta |

- **Sugestão:** c). Justificativa: "seguimos a mesma regra do ADR 0014: não criamos conta que o cliente não pediu; o histórico aparece quando ele se cadastra".

### P3. Canal detalhado (loja, site, app)
- **Por quê:** o faturamento é pedido por canal, e `pedido.canal` só separa físico × online.

| Opção | Ganho | Perda |
|---|---|---|
| a) Coluna nova no pedido (ex.: `origem`: `loja`, `site`, `app`) | Não mexe no estoque por canal (ADR 0004) | Mais uma coluna |
| b) Ampliar `canal` para `site` e `app` | Uma coluna só | `canal` também é o canal do estoque: quebra o ADR 0004 e vários triggers |
| c) Deduzir pelo código do pedido (`S…`, `APP-…`) | Nada muda | Só funciona para os pedidos da Vulto |

- **Sugestão:** a).

### P4. Origem da migração (rastreabilidade)
- **Por quê:** cada registro precisa dizer de onde veio (`vendas#272`, `Clientes!15`) e que regra o mudou.

| Opção | Ganho | Perda |
|---|---|---|
| a) Coluna `linha_origem` em cada tabela migrada | Aparece direto na consulta | Coluna em ~12 tabelas que só a Vulto usa |
| b) Uma tabela `origem_migracao` (tabela, id, linha de origem, valor original) | Um lugar só; não mexe nas tabelas da Entrega 2 | Uma junção para mostrar na demo |

- **Sugestão:** b).

### P5. Fidelidade (Vulto+)
- **Por quê:** "clientes e saldo de pontos" fazem parte da união; o passivo é um dos três números.

| Opção | Ganho | Perda |
|---|---|---|
| a) Movimentos de pontos (crédito, resgate, estorno, ajuste, vencimento) com o saldo calculado, no mesmo padrão do estoque (ADR 0005); saldo da planilha como saldo inicial em 01/01/2025 e os movimentos do banco depois | Histórico completo; o passivo sai do nosso banco e cada ponto tem origem | Mais trabalho (tabelas e trigger) |
| b) Só o saldo final por cliente | Rápido | Não explica o passivo; a banca pergunta "de onde veio esse número?" |
| c) Não migrar; passivo só num relatório | Nada muda no banco | Não atende "uma plataforma só"; as planilhas continuariam em uso |

- **Sugestão:** a). As regras (valor do ponto, vencimento) ficam numa tabela de configuração, com a data em que valem.

### P6. Atendimento externo (WhatsApp, Instagram, e-mail)
- **Por quê:** a gerente quer os atendimentos "no nosso módulo, ligados ao cliente e ao pedido certo".

| Opção | Ganho | Perda |
|---|---|---|
| a) No chamado: `canal` e `contato_externo`, cliente opcional quando vem de fora; na mensagem: autor opcional + nome do autor externo | Os 1.647 entram, inclusive os 408 do Instagram, e a conversa vira mensagens | Ajuste em `chamado` e `mensagem` |
| b) Só o chamado, com a conversa inteira na descrição | Menos trabalho | O histórico fica num bloco só, diferente dos chamados da Lorenzi |
| c) Migrar só os que acham cliente e pedido | Nada muda no banco | Perde mais da metade |

- **Sugestão:** a). Separar as falas pelo `[Atendente]:` (1.105 conversas têm).

### P7. Pagamento e devolução da Vulto
- **Por quê:** `pagamento.metodo` é obrigatório e a Vulto só registra o método na planilha da Batel; a devolução da Vulto é uma venda negativa, e o nosso banco não aceita.

| Opção | Ganho | Perda |
|---|---|---|
| a) Método `nao_informado` só para os migrados | Todo pedido pago tem pagamento; a Batel entra com o método real | Um valor a mais no `metodo` |
| b) Pedido migrado sem linha de pagamento | Não inventa dado | Sem pagamento não há estorno (o estorno aponta para o pagamento original) |

- **Sugestão:** a). A devolução depende da V6.

### P8. Item sem variante (`DIVERSO`, Batel)
| Opção | Ganho | Perda |
|---|---|---|
| a) Um produto e uma variante genéricos ("Item sem cadastro Vulto"), inativos e sem estoque | Nada muda no banco | Não baixa estoque desses itens |
| b) `item_pedido.id_variante` opcional + descrição | Fiel ao original | Mexe numa regra central do pedido |

- **Sugestão:** a). 266 itens `DIVERSO` + as 72 vendas da Batel.

### P9. Funcionários da Vulto como autores
- **Por quê:** a mensagem exige autor e a transferência exige solicitante; vendedor e quem lançou o estoque estão em texto livre (48 grafias de vendedor).

| Opção | Ganho | Perda |
|---|---|---|
| a) Uma conta interna inativa por funcionário (24) | Autoria real | Contas no Auth com e-mail inventado; as grafias não batem com o cadastro |
| b) Uma conta "Migração Vulto" inativa como autor, e o nome original guardado em texto (P4 ou P6) | Uma conta só; nada inventado | Não filtra por vendedor da Vulto |
| c) Descartar a autoria | Simples | Perde quem atendeu e quem vendeu |

- **Sugestão:** b).

---

## Vendas e faturamento (Matias)

### V1. App grava em centavos desde ago/2025
- **Evidência:** `01_vendas` ("O app nem sempre gravou em centavos"): ~2.300 em centavos e ~700 em reais; o preço dos itens × catálogo separa as duas faixas; 24 ambíguas.
- **Opções:** a) "todo app ÷ 100" (erra ~700 vendas); b) **venda por venda, pelo preço dos itens × catálogo e pela faixa do total**; c) cortar pela data de ago/2025 (erra as 15 de jun/2026).
- **Sugestão:** b). As 24 ambíguas entram como margem de erro.

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

### V6. Devolução como venda negativa (201) e troca como venda nova (328)
- **Opções para a devolução:** a) achar o pedido original e lançar devolução + estorno (o nosso modelo, ADR 0015); b) manter como desconto no mês da devolução.
- **Opções para a troca:** a) não é faturamento: só a movimentação de estoque (`saida_troca`); b) conta como venda (regra da Vulto, "registrar como venda nova").
- **Sugestão:** a) e a). Impacto em R$ depois da V1.

### V7. Frete (R$ 49.572,80 na coluna + frete escondido no total do app)
- **Evidência:** REGRAS_OPERACAO ("frete entra no faturamento", com a ressalva da Paula); `01_vendas` (~800 vendas do app com o frete dentro do total).
- **Opções:** a) receita (Diego); b) repasse (Paula); c) **faturamento sem frete, com o frete numa linha à parte**.
- **Sugestão:** c). Responde aos dois, e a diretoria escolhe qual somar.

### V8. Vendas de teste
- **Evidência:** 18 com `obs = teste` (o README fala em 37: conferir).
- **Sugestão:** excluir e listar no registro.

### V9. Batel em março (planilha manual × relançamentos)
- **Evidência:** `06_batel`: 72 vendas na planilha (R$ 28.025,99; total da gerente R$ 27.216,29, já sem a cancelada e as 2 estornadas); 16 relançamentos com a data do relançamento, 9 achando par na planilha pelo CPF.
- **Opções:** a) planilha até o PDV voltar (23/03) + sistema depois, descartando os relançamentos que acham par; b) só o sistema; c) somar os dois.
- **Sugestão:** a). c) duplica; b) perde as vendas não relançadas.

### V10. Ship from store (56)
- **Opções:** a) pedido online despachado pela Pinheiros (o nosso modelo aceita: `despacha_online`), baixando o estoque da Pinheiros; b) baixar do CD, como o sistema fez.
- **Sugestão:** a). É o que aconteceu fisicamente.

---

## Estoque (Matias)

### E1. Qual fonte vale para o saldo de cada unidade
- **Evidência:** `03_estoque`: sistema com 1.156 saldos negativos; a contagem física dá mais que o sistema nas lojas; CD: sistema 28.136 × WMS 6.601. Conflito no case: Diego diz que "o oficial é o sistema"; a diretora diz que "o estoque do sistema é ficção".
- **Opções:** a) sistema; b) **contagem física nas lojas e WMS no CD**; c) o maior dos dois.
- **Sugestão:** b). A Savassi contou por modelo: distribuir pelo sistema dentro do total do modelo (opção a decidir). Eldorado tem 263 peças que estão no sistema e não na contagem: verificar antes.

### E2. Como reconstruir o histórico ("quanto tinha há duas semanas")
- **Evidência:** a reconstrução entradas − vendas desde jan/25 dá saldo negativo (falta saldo inicial e faltam movimentações). O nosso banco **recusa** movimentação que deixe o saldo negativo.
- **Opções:**

| Opção | Ganho | Perda |
|---|---|---|
| a) Saldo inicial na data da contagem e movimentos depois | Simples; nunca fica negativo | Histórico só a partir de set/26 |
| b) Movimentos desde jan/25 a partir de zero | Histórico completo | Quebra no trigger (fica negativo) |
| c) De trás para frente: saldo inicial em 01/01/2025 = contagem − movimentos do período; depois todos os movimentos | Histórico completo **e** bate com a contagem | Onde faltam movimentos, o saldo intermediário pode ficar negativo: precisa de ajuste com motivo |
| d) Só o saldo em 01/10 | Rápido | Não responde à diretora |

- **Sugestão:** c), testando antes por SKU quantos ajustes seriam necessários. Se forem muitos, a) e explicar o limite na banca.

### E3. Estoque do site e do app (cópia do WMS do CD)
- **Sugestão:** não migrar as "lojas" 5 e 6; o canal online do CD vem do WMS. Impacto: evita contar o estoque do CD até três vezes.

### E4. Transferência anotada pelos dois lados
- **Evidência:** REGRAS_OPERACAO ("uma das duas basta"); ~5.400 linhas.
- **Sugestão:** juntar saída e entrada do mesmo SKU, mesma quantidade e datas próximas numa transferência só; a sobra entra sozinha.

### E5. Unidades sem data de contagem (Eldorado, Savassi, Batel)
- **Opções:** a) assumir a mesma data da Pinheiros (20/09); b) assumir 30/09; c) assumir que vale para 01/10.
- **Sugestão:** a decidir com o Matias depois de ver as vendas dessas lojas entre 20 e 30/09 (é o tamanho da margem de erro).

---

## Catálogo (Matias)

### C1. Peça em duas coleções com preço diferente (128)
- **Evidência:** `02_catalogo`: em 127 dos 128, a coleção mais nova é a mais cara (mediana R$ 10).
- **Opções:** a) mais nova (Marina); b) **menor preço (Diego e financeiro, "pra não dar problema com Procon")**.
- **Sugestão:** b). O próprio REGRAS_OPERACAO diz que, em conflito com a planilha da Marina, vale ele. O outro preço vai para o `historico_preco`.

### C2. Código reaproveitado (6 modelos)
- **Sugestão:** ligar cada venda antiga ao produto da coleção vigente na data da venda.

### C3. Descontinuado sem marcação clara (93)
- **Opções:** a) ativo; b) inativo.
- **Sugestão:** a). Desativar depois é fácil; uma peça que some da vitrine não aparece para ninguém revisar.

### C4. Variantes vendidas que não estão no catálogo (452)
- **Evidência:** todas são de modelos que existem; faltam só cores e tamanhos.
- **Sugestão:** criar a variante a partir do modelo, com o preço do modelo e `ativo = false` se não houver estoque.

### C5. Descrição do produto (obrigatória, a Vulto não tem)
- **Sugestão:** usar o nome nas duas descrições e deixar para a Marina completar.

---

## Clientes e Vulto+ (Nicole)

### K1. CPF com 2 ou mais cadastros (131)
- **Evidência:** `04_clientes_pontos`: em 71, os dois cadastros têm pontos; somar acrescenta 49.973 pontos.
- **Opções:** a) **juntar e somar os pontos**; b) ficar com o cadastro mais recente.
- **Sugestão:** a). É dívida com o cliente, e a Head de CRM quer cada pessoa uma vez. Impacto: +49.973 pontos no passivo.

### K2. CPF com dígito verificador errado (28 no cadastro, 134 nos pontos)
- **Sugestão:** o cliente entra sem CPF (P2 permite), e os pontos desses CPFs ficam como órfãos, contados à parte como margem de erro.

### K3. Valor do ponto: R$ 0,10 (sistema) × R$ 0,05 (regra desde mar/2025)
- **Sugestão:** R$ 0,05, a regra vigente, mostrando também o valor a R$ 0,10. O passivo em R$ dobra conforme a escolha.

### K4. Vencimento (12 meses; 18 para Ouro), Ouro +50% e dobro no online
- **Opções:** a) aplicar retroativamente; b) **não aplicar no passado e passar a aplicar a partir da migração**.
- **Sugestão:** b) para o vencimento e o Ouro: o cliente nunca viu ponto vencer. Para o dobro no online gravado errado (`web`, `ecommerce`), corrigir a favor do cliente; o impacto fica a calcular.

### K5. Planilha congelada + banco; antigos também na aba nova (178)
- **Sugestão:** saldo da aba Clientes em 01/01/2025 + movimentos do banco. Os `PONTOS_2023` dos antigos não somam (pela aba LEIA ISSO, os ativos foram migrados em jan/24).

### K6. Pontos indevidos: venda cancelada (367), aguardando (248), pedido duplicado (76), bug do app (4 clientes, 215.982)
- **Sugestão:** estornar os créditos sem venda paga que ainda não foram estornados e os 215.982 pontos dos 4 ainda inflados.

### K7. Resgates só na planilha (~41) e sem CPF (52)
- **Sugestão:** descontar os 41 com CPF; os 52 sem CPF ficam como margem de erro.

### K8. CPFs só no banco de pontos (534)
- **Evidência:** começaram em 2025–2026; não são erro de digitação.
- **Sugestão:** entram como clientes sem conta, com CPF e saldo, sem nome.

### K9. Telefone sem DDD (425) e `aceita_msg` vazio (827)
- **Sugestão:** o telefone fica como está, marcado como incompleto (não inventar DDD). Consentimento vazio = não aceita (LGPD).

---

## Atendimento (Nicole)

### A1. Instagram sem como achar o cliente (408)
- **Sugestão:** migrar com o @ como contato externo (P6), sem cliente.

### A2. Número solto que bate com o id da venda (329)
- **Evidência:** `05_atendimentos`: em 311, a venda é de até 60 dias antes.
- **Sugestão:** ligar os 311; os 18 suspeitos ficam sem pedido.

### A3. Spam (10)
- **Sugestão:** não migrar.

### A4. Categoria, status e encerramento
- **Sugestão:** de-para do `assunto`/`tags` para `entrega`, `troca_devolucao`, `estorno`, `duvida` e `outros`. Concluído vira `concluido` + `resolvido`; o que estava em aberto há mais de 30 dias vira `concluido` + `sem_resposta`.

---

## Unidades (Nicole)

### U1. Endereço das unidades da Vulto (obrigatório, a Vulto não tem)
- **Sugestão:** pedir à Vulto ou usar o endereço público de cada loja; sem isso, a carga das unidades não passa.
