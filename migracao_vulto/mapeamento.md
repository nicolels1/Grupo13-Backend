# Mapeamento: Vulto → Casa Lorenzi

Onde cada dado da Vulto entra no banco da Casa Lorenzi e qual decisão do [registro de decisões](registro_decisoes.md) o resolve. As evidências estão nos notebooks (`notebooks/`).

Legenda: ✅ decidido (cabe como está ou com a regra da decisão citada) · ⚠️ ainda sem decisão no registro · ❌ não migra (descartado, com a decisão citada)

## Quem revisa cada parte

| Parte | Responsável | Pergunta que responde |
|---|---|---|
| 1. Unidades, 3. Clientes, 6. Fidelidade, 7. Atendimento | Nicole | O que a plataforma precisa ter para receber isso? |
| 2. Catálogo, 4. Vendas, 5. Estoque | Matias | Que regra transforma o dado da Vulto no formato do nosso banco? |
| Identificação do cliente (CPF × telefone × @Instagram), SKU oficial, código do pedido citado no atendimento e as decisões que travavam as outras (J1, J2, J3, P2, P7, V2–V5) | Os dois juntos | Atravessam todas as partes |

## Restrições do nosso banco que pesam na migração

Conferidas contra os models (`src/models/`) e as migrations em 19/10/2026.

**Contas**
- **Todo cliente é uma conta com login**: `usuario.id_usuario` aponta para `auth.users` (Supabase Auth); `nome` e `email` são obrigatórios (e-mail único); `tipo_conta = 'cliente'` exige CPF.
- Funcionário é `usuario` com `tipo_conta = 'interna'` e um `modelo_acesso`.

**Unidades e catálogo**
- **Unidade exige endereço completo**: `rua`, `numero`, `bairro`, `cidade`, `uf`, `cep`. A Vulto só tem nome e cidade.
- **Produto exige `descricao_tecnica` e `descricao_cliente`**. A Vulto não tem descrição.
- Variante: `sku` único, `cor` e `tamanho` obrigatórios (sem cor usa `Única`, sem tamanho usa `U`), `preco` ≥ 0, um par cor × tamanho por produto.

**Pedidos**
- `codigo_venda` obrigatório e único (até 20 caracteres). Os códigos da Lorenzi são `CL` + 8 caracteres; os da Vulto (`S123456`, `APP-1234567`) não colidem, mas 10.885 vendas da loja física não têm código.
- `canal` só aceita `loja_fisica` ou `online`; o online exige `modalidade` (`entrega` ou `retirada`) e cliente com conta (`online_exige_cliente`). Venda física sem conta pode ter só o `cpf_nota`.
- **O CD não faz venda física nem retirada** (trigger).
- `status`: `aguardando_pagamento`, `pago`, `enviado`, `pronto_para_retirada`, `entregue` ou `cancelado`.
- Cancelado exige `motivo_cancelamento` (`cliente`, `reserva_vencida`, `retirada_vencida` ou `equipe`); `equipe` exige quem cancelou e justificativa.
- `valor_total` e `valor_frete` ≥ 0. **Não existe campo de desconto.**
- Item: `quantidade` > 0 e precisa de uma `id_variante`.
- Pagamento: `metodo` só aceita `pix`, `cartao_credito`, `cartao_debito` ou `dinheiro`; `valor` > 0; o estorno usa o mesmo método do pagamento original (trigger).

**Estoque**
- Nunca negativo: a movimentação que deixaria o saldo negativo é recusada (`Estoque insuficiente`). **A ordem da carga importa**: o saldo inicial precisa entrar antes das vendas.
- O saldo só muda por movimentação (trigger); o canal é `loja_fisica` ou `online`, e o CD só tem `online`.
- `ajuste`, `avaria` e `perda` exigem `motivo`.
- Transferência exige `id_solicitante` (um usuário).
- **`movimentacao_estoque`, `historico_preco` e `historico_chamado` não podem ser alterados nem apagados.** Uma carga errada não se desfaz com `DELETE`: testar antes num banco de teste.

**Atendimento**
- Chamado exige `id_cliente`; `categoria` só aceita `entrega`, `troca_devolucao`, `estorno`, `duvida` ou `outros`; `status` é `aberto`, `em_andamento` ou `concluido`, e concluído exige `motivo_encerramento` (`resolvido`, `desistencia` ou `sem_resposta`).
- Mensagem exige `id_autor` (um usuário).

## 1. Unidades (Nicole)

Evidência: `banco_lojas`, `banco_funcionarios`, tabela `config` do vulto.db e `notebooks/07_funcionarios.ipynb`. Decisões: U1, P1, P9.

| Vulto | Casa Lorenzi | Situação |
|---|---|---|
| `lojas` 1–4 (Pinheiros, Eldorado, Savassi, Batel) | `unidade`, `tipo = 'loja'` | ✅ endereço "A confirmar" e Pinheiros com `despacha_online` (ship from store), U1 |
| `lojas` 7 (CD Cajamar) | `unidade`, `tipo = 'cd'` | ✅ endereço "A confirmar" (U1) |
| `lojas` 5 (site) e 6 (app) | não são unidades: são o canal `online` do CD | ✅ o pedido vai para o CD Cajamar, canal `online` (V12); o estoque delas não migra (E3) |
| `lojas.cidade` | `unidade.cidade` | ✅ |
| `funcionarios` (24: nome, código, loja, perfil, ativo) | `usuario` `interna` + `modelo_acesso` | ✅ 13 vendedores e 3 do SAC viram conta interna inativa; o resto vira a conta "Migração Vulto" (P9) |
| `funcionarios.perfil` (vendedor, admin, gerente, operador) | `modelo_acesso` | ✅ modelos Vendedor e Atendente (P9) |
| `funcionarios.senha` | — | ❌ descartada (já fica fora no staging) |
| `config.estoque_minimo` (2) | `estoque.estoque_minimo` | ⚠️ sem decisão: aplicar a todas as variantes da Vulto ou deixar vazio |
| `config.prazo_troca_dias` (30) | regra de troca da Lorenzi | ⚠️ sem decisão; a loja aceitava trocas de 31 a 50 dias depois (V6) |
| `config.versao`, `config.senha_master` | — | ❌ descartados |
| — | `unidade.marca` | ✅ coluna `marca` (P1) |

## 2. Catálogo (Matias)

Evidência: `notebooks/02_catalogo.ipynb`. Decisões: J2, C1–C19, P1, P8.

| Vulto | Casa Lorenzi | Situação |
|---|---|---|
| `catalogo.nome` (por modelo) | `produto.nome` | ✅ agrupado pelo código do modelo no SKU (C6); código reaproveitado vira produto com SKU de sufixo (C2) |
| — | `produto.descricao_tecnica`, `descricao_cliente` (obrigatórias) | ✅ o nome nas duas descrições (C5) |
| `catalogo.categoria` | `produto.id_categoria` → `categoria_produto` | ✅ pelo código de categoria do SKU, compartilhada com a Lorenzi (C18) |
| `catalogo.sku`, `cor`, `tamanho` | `variante.sku`, `cor`, `tamanho` | ✅ SKU oficial longo (J2); cor pelo código do SKU com a tabela de letras (C7); mesma peça em dois formatos vira uma variante (C13) |
| `catalogo.preco` | `variante.preco` | ✅ coleção mais nova (C1), mesmo modelo na aba nova para os SKUs da aba antiga (C8), preço praticado quando for maior (C19), colunas trocadas destrocadas (C17) |
| aba da planilha (coleção: Verão 25, Inverno 25, Verão 26, Atemporais) | — | ❌ não vira coluna; serve para o preço e o histórico (C9) |
| aba `PLANILHA ANTIGA NAO USAR` (297) | `variante`, `historico_preco` | ✅ cria os SKUs que não estão em outra aba (C8) |
| preços das coleções antigas | `historico_preco` | ✅ na data de início de cada coleção (C14) |
| `catalogo.descontinuado` | `variante.ativo` (falso só sem estoque; C3) + origem da migração | ✅ as 93 com só o SKU em vermelho também são descontinuadas (C3) |
| `catalogo.revisar_preco` (fundo amarelo, 5) | — | ✅ migra com o preço da planilha e vai para a Marina (C10) |
| variantes vendidas que não estão no catálogo (452) | `variante` criada a partir do modelo | ✅ criadas a partir do modelo (C4) |
| `catalogo.custo` | — | ❌ descartado; usado só para achar as colunas trocadas (C11, C17) |
| `catalogo.ean` | `variante.ean` | ✅ coluna nova e busca do caixa (C12) |
| — | `produto.marca` | ✅ coluna `marca` (P1) |

## 3. Clientes (Nicole)

Evidência: `notebooks/04_clientes_pontos.ipynb`. Decisões: J1, P2, K1, K2, K8, K9.

| Vulto | Casa Lorenzi | Situação |
|---|---|---|
| `Clientes.Nome`, `cpf`, `E-mail` | cliente sem conta (P2) | ✅ cliente sem conta, que passa para a conta quando a pessoa se cadastra (P2); CPF corrigido pela K2 |
| cliente sem e-mail | cliente sem conta (P2) | ✅ o cliente sem conta exige CPF ou e-mail, não os dois (P2) |
| CPFs com 2+ cadastros (131) | um cliente por CPF | ✅ um cliente por CPF, somando os pontos (K1) |
| CPFs só no banco de pontos (534) | cliente sem conta (P2) | ✅ cliente sem conta, com CPF e saldo, sem nome (K8) |
| `Tel`, `aceita_msg`, `categoria`, `loja_cadastro`, `Data Cadastro` | cliente sem conta (P2) e fidelidade (P5) | ✅ telefone completado pelo DDD de outra fonte (K9a), consentimento em três estados (K9b), categoria do Vulto+ (K4c), data e loja do cadastro mais antigo (K1) |
| `Clientes.Obs` | — | ⚠️ "não ligar" vale como recusa (K9b); as outras anotações não têm decisão |
| `clientes antigos` (300: nome, CPF/CNPJ, pontos 2023, última compra, status) | cliente sem conta (P2) | ✅ vale o saldo da aba nova para quem está nas duas (K5c); os 122 só na aba antiga entram como clientes (K5d) |
| `clientes antigos.CPF/CNPJ` com CNPJ | — | ✅ nenhum CNPJ na aba: os 300 têm 11 dígitos |
| `clientes antigos.STATUS` (ativo, inativo, bloqueado ×46) | saldo inicial (K5d) | ✅ todos entram como clientes; só os ATIVOS levam o saldo de 2023 (K5d) |

## 4. Vendas (Matias)

Evidência: `notebooks/01_vendas.ipynb` e `notebooks/06_batel.ipynb`. Decisões: J1, V1–V19, P3, P7, P8, P9.

| Vulto | Casa Lorenzi | Situação |
|---|---|---|
| `vendas` (um pedido) | `pedido` | ✅ |
| `vendas.loja_id` | `pedido.id_unidade` | ✅ lojas 1–4 → a loja; 5 e 6 → CD Cajamar (V12); ship from store pela Pinheiros (V10) |
| `vendas.canal` + `loja_id` | `pedido.canal` (`loja_fisica` / `online`) | ✅ o canal sai do `loja_id`, e a coluna `canal` é descartada (V12) |
| site × app | `pedido.canal_venda` | ✅ coluna `canal_venda`, pelo código do pedido (P3) |
| — | `pedido.modalidade` (obrigatória no online) | ✅ `entrega` (V11) |
| `vendas.total` | `pedido.valor_total` | ✅ centavos corrigidos venda a venda em todo pedido `APP-` (V1) |
| `vendas.desconto` (2.898 vendas) | `pedido.valor_desconto` | ✅ coluna nova; o resgate de pontos fica ligado ao pedido (V18) |
| `vendas.frete` (e o frete escondido do app) | `pedido.valor_frete` | ✅ fora do faturamento, numa linha à parte (V7) |
| `vendas.status` | `pedido.status` | ✅ pago → `entregue` (V13); vazio conta como pago (V2); aguardando → cancelado (V3); estornado e chargeback → pago + estorno (V4); cópias não migram (V5); canceladas não contam (V19) |
| cancelado | `pedido.status = 'cancelado'` + `motivo_cancelamento` | ✅ `equipe`, com a conta "Migração Vulto" (V14) |
| `vendas.criado_em` | `pedido.criado_em` / `pago_em` | ✅ mês pelo horário de Brasília (V16); entregue_em vazio no online (V13) |
| `vendas.cliente_ref` | `pedido.id_cliente` ou `cpf_nota` | ✅ CPF, e-mail ou telefone pelas camadas da J1 (J1a, J1b); sem CPF, cliente sem conta pelo e-mail (P2) |
| `vendas.pedido_externo` / `id` | `pedido.codigo_venda` | ✅ mantém `S…` e `APP-…`; a loja física recebe `VLT-<id>` (V15) |
| `vendas.vendedor` | `pedido.id_registrado_por` | ✅ conta interna inativa do vendedor, pela grafia e a loja (P9) |
| `vendas.compra_id` (7.665) | origem da migração (P4) | ✅ fica na origem da migração (P4), para ligar o crédito de pontos à venda (J1c, K6) |
| `vendas.obs` (troca, devolução, sfs) | `pedido.devolucao`, `pagamento` de estorno, `movimentacao` | ✅ devolução e troca (V6), teste (V8), relançamento da Batel (V9), ship from store (V10) |
| `itens_venda.sku`, `qtd`, `preco_unit` | `item_pedido` | ✅ `qtd = -1` é devolução (V6); `DIVERSO` vai para o item genérico (P8); centavos do app (V1) |
| `itens_venda.descricao` | — | ✅ separa a peça antiga da nova no código reaproveitado (C2) |
| forma de pagamento | `pagamento.metodo` | ✅ `nao_informado`, fora a Batel (P7) |
| vendas manuais da Batel (`DATA`, `HORA`, `VALOR`) | `pedido` `loja_fisica` | ✅ planilha para 02–22/03; os relançamentos da gerente não migram; datas trocadas pelo Excel voltam para março (V9) |
| Batel `CLIENTE`, `CPF` | `pedido.id_cliente` ou `cpf_nota` | ✅ pela J1 (V9) |
| Batel `PEÇAS`, `QTD` | `item_pedido` | ✅ peça achada pelo texto; o resto vai para o item genérico (V9, P8) |
| Batel `PGTO` (crédito 2x, débito, dinheiro, pix, vazio ×4) | `pagamento.metodo` | ✅ "crédito 2x" → `cartao_credito`; vazio → `nao_informado` (V9, P7) |
| Batel `VENDEDORA`, `OBS` | `pedido.id_registrado_por` | ✅ vendedora pela P9; a `OBS` só classifica (V9) |

## 5. Estoque (Matias)

Evidência: `notebooks/03_estoque.ipynb`. Decisões: J2, E1–E13, V10.

| Vulto | Casa Lorenzi | Situação |
|---|---|---|
| `estoque` (saldo atual) | `estoque` (calculado pelas movimentações) | ❌ não entra: vale a contagem nas lojas e o WMS no CD (E1) |
| `estoque.atualizado_em` | — | ❌ não entra (E1) |
| `entradas_estoque` (sku, loja, qtd, data) | `movimentacao_estoque` (`recebimento`) e `transferencia` | ✅ histórico reconstruído de trás para frente (E2); saída e entrada da mesma transferência juntas, saídas do CD lidas do texto (E4); em trânsito em 01/10 viram transferência `enviada` (E13) |
| `entradas_estoque.custo_unit`, `nf`, `fornecedor` | — | ❌ descartados (E9) |
| `entradas_estoque.usuario`, `obs` | `movimentacao_estoque.id_usuario`, `motivo` | ✅ o lançamento fica sem usuário (opcional, P9); a `obs` dá o tipo e o destino (E4) |
| vendas | `movimentacao_estoque` (`venda`) | ✅ movimentação a partir das vendas, no histórico (E2); ship from store baixa da Pinheiros (V10); cancelada gera `venda` + `retorno_cancelamento` (V17) |
| contagem física (`codigo`, `quantidade`, `data_contagem`) | `movimentacao_estoque` (`saldo_inicial` ou `ajuste`) | ✅ ponto de partida do saldo (E1, E2); contagem sem data em 20/09 com faixa (E5) |
| contagem `nivel = modelo` (161, Savassi) | — | ✅ distribuída pelas variantes na proporção do sistema (E6) |
| contagem `reservado` | `estoque.quantidade_reservada` | ✅ zerado; vale o saldo do WMS (E8) |
| contagem `observacao` | `movimentacao_estoque.motivo` | ✅ "faltou contar provador" e "recontar" ficam como margem de erro (E11) |
| estoque do site e do app | canal `online` do CD | ✅ não migra (E3) |
| saldo negativo | — | ✅ não entra: o saldo vem da contagem (E1); onde o histórico ficaria negativo, entra um ajuste (E2) |
| lojas físicas | canal `loja_fisica`; parte vende online? | ✅ `loja_fisica`; a Pinheiros também tem `online`, separado pelo ship from store (E7, U1) |

## 6. Fidelidade, Vulto+ (Nicole)

Evidência: `notebooks/04_clientes_pontos.ipynb`. Decisões: P5, K1–K9, J1c, J1d.

| Vulto | Casa Lorenzi | Situação |
|---|---|---|
| `clientes.Pontos` (congelado em 01/01/2025) + `pontos_mov` | movimentos de pontos (P5) | ✅ saldo da planilha como movimento inicial em 01/01/2025 + movimentos do banco (K5a, K5b, K1, K5e) |
| `pontos_mov.tipo` (C crédito, D débito, E, AJ ajuste) | movimentos de pontos (P5) | ✅ crédito, resgate, estorno e ajuste (P5); créditos indevidos estornados (K6); dobro do online que faltou (K4b); negativos zerados (K5f) |
| `pontos_mov.compra_id`, `obs` | origem da migração (P4) | ✅ origem da migração (P4); crédito em telefone ou CPF errado vai para o dono (J1c, J1d, K2) |
| `compras` (14.948: cliente, valor, loja, origem pdv/site/app) | — | ✅ não vira tabela: é a base do crédito, e o cliente sai pelas camadas da J1 (J1c) |
| `clientes antigos.PONTOS_2023` | saldo inicial (P5) | ✅ só os 31 ATIVOS que não estão na aba nova (K5c, K5d) |
| `Resgates` (596: data, cliente, pontos, desconto R$, loja, atendente) | movimentos de pontos (P5) | ✅ os que não estão no banco viram resgate (K7a); ponto de milhar lido certo (K7b) |
| `config.valor_ponto` (0,10) e `validade_dias` (365) | regras de fidelidade (P5, K4a) | ✅ R$ 0,10 até fev/2025 e R$ 0,05 depois, pela data do ponto (K3); vencimento só a partir de 01/10/2026 (K4a) |
| outras regras (dobro no online, Ouro +50%) | regras de fidelidade (P5, K4a) | ✅ regras por marca a partir da migração (K4a); categoria do Vulto+ (K4c) |

## 7. Atendimento (Nicole)

Evidência: `notebooks/05_atendimentos.ipynb` (seção 5: cliente × pedido). Decisões: J1, J3, P2, P6, P9, A1–A8.

| Vulto | Casa Lorenzi | Situação |
|---|---|---|
| `atendimentos` (uma conversa) | `chamado` | ✅ 1.625 migram, sem 15 spams e 7 "teste" (A3) |
| `data_hora` | `chamado.criado_em` | ✅ |
| `assunto` / `tags` | `chamado.categoria` + `assunto` | ✅ categoria pela tag, com a categoria nova `pagamento`; assunto fixo por tag (A4a) |
| `status` | `chamado.status` + `motivo_encerramento` | ✅ concluído → `concluido` + `resolvido`; em aberto desde 01/09/2026 → `aberto`; o resto → `concluido` + `sem_resposta` (A4b) |
| `pedido_codigo` | `chamado.id_pedido` | ✅ 928 acham a venda pelas regras da J3 (J3, A2) |
| `mensagem` (primeira fala) | `chamado.id_pedido` | ✅ o pedido citado só no texto também liga (J3) |
| `contato` (telefone, e-mail, @Instagram) | `chamado.contato_externo` e cliente (P2) | ✅ o telefone e o e-mail acham o CPF (J1a); o @ fica como contato externo (A1, P6); sem cliente, entra sem cliente (A6, A7) |
| dono do pedido (`vendas.cliente_ref` da venda citada) | `chamado.id_cliente` | ✅ vem antes do contato; 1.109 dos 1.625 com cliente (J1a, A1) |
| `nome_contato` | `mensagem.nome_autor_externo` | ✅ não liga ao cliente; vira o nome de quem falou (A5, P6) |
| `mensagem` (conversa inteira num campo) | `chamado.descricao` + `mensagem` (uma por fala) | ✅ a primeira fala vira a descrição e as outras viram mensagens (P6) |
| `canal` (WhatsApp, Instagram, e-mail) | `chamado.canal_externo` | ✅ coluna nova (P6) |
| `atendente` | `mensagem.id_autor`, `chamado.id_responsavel` | ✅ Júlia, Renata e Marcos viram conta interna inativa; bot e `time insta`, a "Migração Vulto" (P9) |

## Dados que o nosso banco exige e não existem em nenhuma fonte da Vulto

| Dado | Onde é obrigatório | Saída decidida |
|---|---|---|
| Endereço das 5 unidades | `unidade` | O que se deduz do nome e da cidade; o resto "A confirmar" (U1) |
| Descrição dos produtos | `produto` | O nome do produto (C5) |
| E-mail de parte dos clientes | `usuario` | Cliente sem conta, que aceita só o CPF (P2) |
| Motivo do cancelamento | `pedido` | `equipe` com a conta "Migração Vulto" (V14) |
| Modalidade do pedido online | `pedido` | `entrega` (V11) |
| Método de pagamento (fora a Batel) | `pagamento` | `nao_informado` (P7) |
| Autor de mensagem e de transferência | `mensagem`, `transferencia` | Contas internas da P9; a fala do cliente fica sem autor (P6) |

## O que a plataforma precisa ganhar

Lista final da etapa 5, tirada do [registro de decisões](registro_decisoes.md). O detalhe e o porquê de cada item estão na linha da decisão.

### Mudanças no banco e no código

| Decisão | O quê | Onde |
|---|---|---|
| P1 | Coluna **`marca`** (`lorenzi` ou `vulto`) na unidade e no produto; o que já existe recebe `lorenzi` | `unidade`, `produto` |
| P2 | Tabela de **cliente sem conta** (CPF, nome, e-mail, telefone, consentimento, origem), com chave opcional em pedido e chamado; `online_exige_cliente` passa a aceitar o cliente sem conta; ao criar a conta pelo site, o que estiver no CPF ou no e-mail passa para ela | tabela nova; `pedido`, `chamado`, cadastro |
| P3 | Coluna **`canal_venda`** no pedido (`loja`, `site`, `app`), sem mexer no `canal` do estoque | `pedido` |
| P4 | Tabela **`origem_migracao`**: registro na plataforma, fonte e linha de origem, campo, valor original e a decisão que o mudou | tabela nova |
| P5 | **Fidelidade**: movimentos de pontos (crédito, resgate, estorno, ajuste e vencimento) com o saldo por trigger, como o estoque; tabela de regras com a data em que valem (valor do ponto da K3) | tabelas novas |
| K4a | A tabela de regras da P5 ganha a **marca**, o vencimento, o bônus por categoria e o multiplicador por canal | regras de fidelidade |
| P6 | **Atendimento externo**: `chamado.canal_externo` e `chamado.contato_externo`, `mensagem.nome_autor_externo`; autor da mensagem opcional; ajuste em `consulta_mensagens` e nas não lidas | `chamado`, `mensagem` |
| A4a | Categoria **`pagamento`** no chamado (regra do banco, modelo e front), também para os chamados novos | `chamado` |
| P7 | Valor **`nao_informado`** em `pagamento.metodo` e **`migracao`** em `pagamento.origem`, aceitos só em pedido migrado | `pagamento` |
| V18 | Coluna **`valor_desconto`** no pedido | `pedido` |
| C12 | Coluna **`ean`** na variante e a busca do caixa pelo código de barras | `variante`, caixa |

Sugestão ainda não registrada como decisão: filtro `marca` em `GET /produtos` e `GET /categorias` (esta só com as categorias que têm produto da marca), para a vitrine poder mostrar cada rede separada sem duplicar as categorias (P1, C18).

### Dados que a carga cria, sem mudar o banco

| Decisão | O quê |
|---|---|
| P9 | 17 contas internas inativas: 13 vendedores, 3 do SAC e "Migração Vulto" |
| P8 | Produto e variante genéricos "Item sem cadastro Vulto", inativos e sem estoque |
| U1 | 5 unidades com o endereço "A confirmar"; a Pinheiros como loja despachante (`despacha_online`) |
| E7 | Estoque `online` da Pinheiros, separado da contagem |
| C16 | Imagem genérica da marca Vulto em cada produto |
| C18 | Categorias que a Lorenzi não tem (ex.: Tricô) |
| C2 | Produto da peça antiga, com SKU de sufixo, nos 6 códigos reaproveitados |

### Descartados (decidido)

Custo (C11), fornecedor e nota fiscal das entradas (E9), coleção como coluna (C9) e senha dos funcionários. O parcelamento da Batel vira `cartao_credito` (V9).

## Cobertura: todas as colunas de todas as fontes

| Fonte | Tabela / aba | Colunas | Onde está no mapeamento |
|---|---|---|---|
| vulto.db | `lojas` | id, nome, cidade | 1 |
| vulto.db | `funcionarios` | id, codigo, nome, loja_id, senha, perfil, ativo | 1 |
| vulto.db | `config` | valor_ponto, validade_dias, versao, prazo_troca_dias, estoque_minimo, senha_master | 1 e 6 |
| vulto.db | `vendas` | id, pedido_externo, canal, loja_id, cliente_ref, vendedor, total, desconto, frete, status, criado_em, obs, compra_id | 4 |
| vulto.db | `itens_venda` | id, venda_id, sku, descricao, qtd, preco_unit | 4 |
| vulto.db | `compras` | id, cliente_ref, valor, canal, loja_id, criado_em, origem | 6 |
| vulto.db | `pontos_mov` | id, cpf, tipo, qtd, compra_id, criado_em, obs | 6 |
| vulto.db | `estoque` | sku, loja_id, qtd, atualizado_em | 5 |
| vulto.db | `entradas_estoque` | id, sku, loja_id, qtd, custo_unit, nf, fornecedor, data, obs, usuario | 5 |
| clientes_fidelidade | `Clientes` | Nome, cpf, E-mail, Tel, Data Cadastro, categoria, Pontos, Obs, aceita_msg, loja_cadastro | 3 e 6 |
| clientes_fidelidade | `clientes antigos (NAO APAGAR)` | NOME COMPLETO, CPF/CNPJ, PONTOS_2023, ULT_COMPRA, STATUS | 3 e 6 |
| clientes_fidelidade | `Resgates` | data, cliente, pontos usados, desconto R$, loja, atendente | 6 |
| catálogo | 5 abas de produtos | sku, nome, categoria, cor, tamanho, preco, custo, ean (+ formatação: descontinuado, revisar preço) | 2 |
| contagem_set26 | 5 abas (uma por unidade) | unidade, data, nível, código, quantidade, reservado, observação | 5 |
| vendas_batel_MANUAL | `MARÇO` | DATA, HORA, CLIENTE, CPF, PEÇAS, QTD, VALOR, PGTO, VENDEDORA, OBS | 4 |
| atendimentos (CSV) | — | id, data_hora, canal, contato, nome_contato, assunto, mensagem, status, atendente, pedido, tags | 7 |

Não são dados a migrar, mas servem de regra ou conferência: `REGRAS_OPERACAO.txt`, abas `LEIA ISSO` (clientes e catálogo), `Plan1` (contagem por categoria do Vulto+: Bronze 300, Prata 140, Ouro 61, vip? 12) e `Plan2` da Batel (anotações de conferência com a maquininha).
