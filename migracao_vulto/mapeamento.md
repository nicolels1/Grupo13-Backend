# Mapeamento: Vulto → Casa Lorenzi

Onde cada dado da Vulto entra no banco da Casa Lorenzi. **Rascunho para discutir em dupla**: nada aqui está decidido. As evidências estão nos notebooks (`notebooks/`); as opções de cada ⚠️ e ❌ estão em [decisões em aberto](decisoes_em_aberto.md) e, depois de decididas, vão para o [registro de decisões](registro_decisoes.md).

Legenda: ✅ cabe como está · ⚠️ cabe com uma regra de tradução (decisão na etapa 4) · ❌ não existe no nosso banco (a plataforma precisa ganhar ou o dado é descartado)

## Quem revisa cada parte

| Parte | Responsável | Pergunta que responde |
|---|---|---|
| 1. Unidades, 3. Clientes, 6. Fidelidade, 7. Atendimento | Nicole | O que a plataforma precisa ter para receber isso? |
| 2. Catálogo, 4. Vendas, 5. Estoque | Matias | Que regra transforma o dado da Vulto no formato do nosso banco? |
| Identificação do cliente (CPF × telefone × @Instagram), SKU oficial, código do pedido citado no atendimento e as decisões que travam as outras (bloco 1 das [decisões em aberto](decisoes_em_aberto.md#ordem)) | Os dois juntos | Atravessam todas as partes |

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
| `lojas` 7 (CD Cajamar) | `unidade`, `tipo = 'cd'` | ⚠️ mesmo problema do endereço |
| `lojas` 5 (site) e 6 (app) | não são unidades: são o canal `online` do CD | ⚠️ |
| `lojas.cidade` | `unidade.cidade` | ✅ |
| `funcionarios` (24: nome, código, loja, perfil, ativo) | `usuario` `interna` + `modelo_acesso` | ⚠️ criar conta ou só guardar o nome? São autores de venda, entrada de estoque, resgate e atendimento |
| `funcionarios.perfil` (vendedor, admin, gerente, operador) | `modelo_acesso` | ⚠️ de-para para os modelos da Lorenzi |
| `funcionarios.senha` | — | descartar (já fica fora no staging) |
| `config.estoque_minimo` (2) | `estoque.estoque_minimo` | ⚠️ aplicar a todas as variantes da Vulto ou deixar vazio |
| `config.prazo_troca_dias` (30) | regra de troca da Lorenzi | ⚠️ comparar com o prazo da Lorenzi |
| `config.versao`, `config.senha_master` | — | descartar |
| — | marca da unidade (Lorenzi × Vulto) | ❌ |

## 2. Catálogo (Matias)

Evidência: `notebooks/02_catalogo.ipynb`. Decisões: J2, C1–C5, P1, P8.

| Vulto | Casa Lorenzi | Situação |
|---|---|---|
| `catalogo.nome` (por modelo) | `produto.nome` | ⚠️ nome em maiúsculas/minúsculas; código reaproveitado (6 modelos) |
| — | `produto.descricao_tecnica`, `descricao_cliente` (obrigatórias) | ⚠️ a Vulto não tem: texto padrão ou o próprio nome? |
| `catalogo.categoria` | `produto.id_categoria` → `categoria_produto` | ⚠️ de-para de 57 grafias |
| `catalogo.sku`, `cor`, `tamanho` | `variante.sku`, `cor`, `tamanho` | ⚠️ SKU em 3 formatos: escolher o oficial; 65 grafias de cor |
| `catalogo.preco` | `variante.preco` | ⚠️ 128 SKUs com dois preços (mais nova × menor) |
| aba da planilha (coleção: Verão 25, Inverno 25, Verão 26, Atemporais) | — | ❌ não temos coleção (só serve para a regra do preço) |
| aba `PLANILHA ANTIGA NAO USAR` (297) | `historico_preco` ou descartar | ⚠️ |
| preços das coleções antigas | `historico_preco` | ⚠️ |
| `catalogo.descontinuado` | `variante.ativo` (falso só sem estoque; C3) + origem da migração | ✅ as 93 com só o SKU em vermelho também são descontinuadas (C3) |
| `catalogo.revisar_preco` (fundo amarelo, 5) | — | ⚠️ pendência para a Marina |
| variantes vendidas que não estão no catálogo (452) | `variante` criada a partir do modelo | ⚠️ |
| `catalogo.custo` | — | ❌ não guardamos custo (e o oficial é o custo médio, que não está na planilha) |
| `catalogo.ean` | — | ❌ (ou descartar) |
| — | marca do produto | ❌ |

## 3. Clientes (Nicole)

Evidência: `notebooks/04_clientes_pontos.ipynb`. Decisões: J1, P2, K1, K2, K8, K9.

| Vulto | Casa Lorenzi | Situação |
|---|---|---|
| `Clientes.Nome`, `cpf`, `E-mail` | `usuario` (`tipo_conta = 'cliente'`) | ❌ exige login no Supabase Auth: ~2.500 clientes sem senha |
| cliente sem e-mail | `usuario.email` (obrigatório) | ⚠️ |
| CPFs com 2+ cadastros (131) | um `usuario` por CPF | ⚠️ qual cadastro fica |
| CPFs só no banco de pontos (534) | — | ⚠️ sem nome nem contato |
| `Tel`, `aceita_msg`, `categoria`, `loja_cadastro`, `Data Cadastro` | — | ❌ (telefone, consentimento LGPD, categoria do Vulto+); o telefone é o que liga venda e atendimento ao cliente na migração (J1) |
| `Clientes.Obs` | — | ❌ (ou descartar) |
| `clientes antigos` (300: nome, CPF/CNPJ, pontos 2023, última compra, status) | `usuario` (170 também na aba nova) | ⚠️ risco de cliente e pontos em dobro |
| `clientes antigos.CPF/CNPJ` com CNPJ | — | ⚠️ a Lorenzi só tem CPF |
| `clientes antigos.STATUS` (ativo, inativo, bloqueado ×46) | `usuario.status_conta` | ⚠️ bloqueado não existe |

## 4. Vendas (Matias)

Evidência: `notebooks/01_vendas.ipynb` e `notebooks/06_batel.ipynb`. Decisões: J1, V1–V10, P3, P7, P8, P9.

| Vulto | Casa Lorenzi | Situação |
|---|---|---|
| `vendas` (um pedido) | `pedido` | ✅ |
| `vendas.loja_id` | `pedido.id_unidade` | ⚠️ site e app → CD |
| `vendas.canal` + `loja_id` | `pedido.canal` (`loja_fisica` / `online`) | ⚠️ |
| site × app | — | ❌ o faturamento é pedido **por canal**, e o nosso só separa físico × online |
| — | `pedido.modalidade` (obrigatória no online) | ⚠️ a Vulto não registra entrega × retirada |
| `vendas.total` | `pedido.valor_total` | ⚠️ centavos no app desde ago/2025 |
| `vendas.desconto` (2.898 vendas) | — | ⚠️ o total já vem com o desconto descontado em 2.655 delas; perde-se só o valor do desconto |
| `vendas.frete` (e o frete escondido do app) | `pedido.valor_frete` | ⚠️ |
| `vendas.status` | `pedido.status` | ⚠️ de-para (vazio, aguardando, chargeback, estornado...) |
| cancelado | `pedido.status = 'cancelado'` + `motivo_cancelamento` | ⚠️ a Vulto não tem motivo |
| `vendas.criado_em` | `pedido.criado_em` / `pago_em` | ✅ |
| `vendas.cliente_ref` | `pedido.id_cliente` ou `cpf_nota` | ⚠️ online exige conta (ver Clientes); pela J1a, sem CPF o e-mail identifica um cliente sem conta (P2): 1.934 vendas online de 1.235 pessoas; só telefone fora do cadastro entra sem cliente; o atendimento herda esse cliente |
| `vendas.pedido_externo` / `id` | `pedido.codigo_venda` | ⚠️ manter o número original: 928 atendimentos acham a venda por ele (J3); a loja física não tem código, e o atendimento a acha pelo `id` da Vulto (guardar na origem, P4) |
| `vendas.vendedor` | `pedido.id_registrado_por` | ❌ vendedores da Vulto não têm conta (ou descartar) |
| `vendas.compra_id` (7.665) | — | ⚠️ liga a venda à compra que gerou pontos (ver Fidelidade) |
| `vendas.obs` (troca, devolução, sfs) | `pedido.devolucao`, `pagamento` de estorno, `movimentacao` | ⚠️ |
| `itens_venda.sku`, `qtd`, `preco_unit` | `item_pedido` | ⚠️ `qtd = -1` (devolução) não é aceito; `DIVERSO` sem variante |
| `itens_venda.descricao` | — | ⚠️ só ajuda a achar a variante quando o SKU não bate |
| forma de pagamento | `pagamento.metodo` | ❌ a Vulto não registra (só a planilha da Batel) |
| vendas manuais da Batel (`DATA`, `HORA`, `VALOR`) | `pedido` `loja_fisica` | ⚠️ datas ambíguas; risco de duplicar com o sistema |
| Batel `CLIENTE`, `CPF` | `pedido.id_cliente` ou `cpf_nota` | ⚠️ |
| Batel `PEÇAS`, `QTD` | `item_pedido` | ⚠️ peças sem SKU: pedido sem `item_pedido`? |
| Batel `PGTO` (crédito 2x, débito, dinheiro, pix, vazio ×4) | `pagamento.metodo` | ⚠️ parcelas não existem no nosso banco |
| Batel `VENDEDORA`, `OBS` | — | ❌ (ou descartar) |

## 5. Estoque (Matias)

Evidência: `notebooks/03_estoque.ipynb`. Decisões: J2, E1–E5, V10.

| Vulto | Casa Lorenzi | Situação |
|---|---|---|
| `estoque` (saldo atual) | `estoque` (calculado pelas movimentações) | ⚠️ não dá para gravar o saldo direto: precisa virar movimentação |
| `estoque.atualizado_em` | — | ⚠️ data do saldo: usar para saber de quando é cada número |
| `entradas_estoque` (sku, loja, qtd, data) | `movimentacao_estoque` (`recebimento`) e `transferencia` | ⚠️ transferência anotada por um lado só; transferência exige solicitante |
| `entradas_estoque.custo_unit`, `nf`, `fornecedor` | — | ❌ não guardamos custo, nota nem fornecedor (ou descartar) |
| `entradas_estoque.usuario`, `obs` | `movimentacao_estoque.id_usuario`, `motivo` | ⚠️ usuário sem conta |
| vendas | `movimentacao_estoque` (`venda`) | ⚠️ |
| contagem física (`codigo`, `quantidade`, `data_contagem`) | `movimentacao_estoque` (`saldo_inicial` ou `ajuste`) | ⚠️ |
| contagem `nivel = modelo` (161, Savassi) | — | ⚠️ quantidade por modelo, sem cor e tamanho |
| contagem `reservado` | `estoque.quantidade_reservada` | ⚠️ no nosso banco só o online tem reserva |
| contagem `observacao` | `movimentacao_estoque.motivo` | ⚠️ |
| estoque do site e do app | canal `online` do CD | ⚠️ é o CD copiado: não somar |
| saldo negativo | — | ⚠️ o nosso banco não aceita |
| lojas físicas | canal `loja_fisica`; parte vende online? | ⚠️ |

## 6. Fidelidade, Vulto+ (Nicole)

Evidência: `notebooks/04_clientes_pontos.ipynb`. Decisões: P5, K1–K7.

| Vulto | Casa Lorenzi | Situação |
|---|---|---|
| `clientes.Pontos` (congelado em 01/01/2025) + `pontos_mov` | — | ❌ não existe nada de pontos no nosso banco |
| `pontos_mov.tipo` (C crédito, D débito, E, AJ ajuste) | — | ❌ |
| `pontos_mov.compra_id`, `obs` | — | ❌ |
| `compras` (14.948: cliente, valor, loja, origem pdv/site/app) | — | ❌ base do crédito de pontos; parte liga a uma venda por `compra_id` |
| `clientes antigos.PONTOS_2023` | — | ❌ |
| `Resgates` (596: data, cliente, pontos, desconto R$, loja, atendente) | — | ❌ |
| `config.valor_ponto` (0,10) e `validade_dias` (365) | — | ❌ regra mudou para R$ 0,05 desde mar/2025 |
| outras regras (dobro no online, Ouro +50%) | — | ❌ |

## 7. Atendimento (Nicole)

Evidência: `notebooks/05_atendimentos.ipynb` (seção 5: cliente × pedido). Decisões: J3, P2, P6, P9, A1–A6; já decididas: J1, A7 e A8 (registro).

| Vulto | Casa Lorenzi | Situação |
|---|---|---|
| `atendimentos` (uma conversa) | `chamado` | ⚠️ 1.625 a migrar (sem 15 spam e 7 "teste", A3) |
| `data_hora` | `chamado.criado_em` | ✅ |
| `assunto` / `tags` | `chamado.categoria` + `assunto` | ⚠️ de-para pela tag para `entrega`, `troca_devolucao`, `duvida`, `outros` e a categoria nova `pagamento` (A4a) |
| `status` | `chamado.status` + `motivo_encerramento` | ⚠️ |
| `pedido_codigo` | `chamado.id_pedido` | ⚠️ com as regras da J3, 928 acham a venda: código (397), número solto até 60 dias antes (399), app sem `APP-` (89), dígitos invertidos (43); 32 não acham |
| `mensagem` (primeira fala) | `chamado.id_pedido` | ⚠️ 282 têm o pedido só no texto, com a coluna `pedido` vazia (J3) |
| `contato` (telefone, e-mail, @Instagram) | `chamado.id_cliente` | ❌ exige conta; pelo contato, 580 acham um cliente do cadastro e 28 o e-mail de quem comprou (J1a); o @Instagram nunca acha |
| dono do pedido (`vendas.cliente_ref` da venda citada) | `chamado.id_cliente` | ⚠️ vem antes do contato (o pedido do chamado é do próprio cliente); com o contato, 1.109 dos 1.625 têm cliente (com a K2): 808 com CPF do cadastro, 115 com CPF fora dele e 186 só pelo e-mail (cliente sem conta, P2); quando os dois existem (352), sempre concordam; 34 fazem a ponte e-mail ↔ CPF; 69 vendas sem cliente |
| `nome_contato` | — | ⚠️ 39 nomes completos iguais aos de um único cliente (A5) |
| `mensagem` (conversa inteira num campo) | `chamado.descricao` + `mensagem` (uma por fala) | ⚠️ separar pelo `[Atendente]:` |
| `canal` (WhatsApp, Instagram, e-mail) | — | ❌ |
| `atendente` | `mensagem.id_autor`, `chamado.id_responsavel` | ❌ atendentes da Vulto sem conta |

## Dados que o nosso banco exige e não existem em nenhuma fonte da Vulto

| Dado | Onde é obrigatório | Saída possível |
|---|---|---|
| Endereço das 5 unidades | `unidade` | Pedir à Vulto (é informação pública das lojas) |
| Descrição dos produtos | `produto` | Texto padrão ou o nome; a Marina completa depois |
| E-mail de parte dos clientes | `usuario` | Depende do P2 |
| Motivo do cancelamento | `pedido` | Um motivo fixo para os migrados |
| Modalidade do pedido online | `pedido` | Assumir `entrega` |
| Método de pagamento (fora a Batel) | `pagamento` | Depende do P7 |
| Autor de mensagem e de transferência | `mensagem`, `transferencia` | Depende do P6 |

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
