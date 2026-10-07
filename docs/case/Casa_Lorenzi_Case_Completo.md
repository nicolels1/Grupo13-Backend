# Casa Lorenzi — Case de Tech (Trainee Insper Jr. 2026.2)

Documento consolidado com o contexto do case, as decisões tomadas ao longo do projeto e o modelo de dados. Atualizado em 07/10/2026 com os ajustes da Entrega 2 (histórico e tela inicial), a implementação de vendas, avaliações e arquivos e o CPF na nota.

---

## 1. Contexto do case

- **Programa:** última fase do Programa de Trainee da Insper Jr. (empresa júnior de consultoria do Insper), frente de Tech.
- **Cenário:** PMI (Post-Merger Integration) fictício entre a **Casa Lorenzi**, rede tradicional de varejo de moda, e a **Vulto**, rede jovem e omnichannel.
- **Estrutura:** 4 entregas sequenciais de Tech.
- **Entrega 1 — Plano de Execução:** entrega em 08/09/2026, banca em 09/09/2026.
- **Entrega 2 — Desenvolvimento da Plataforma:** enviada em 04/10/2026; entrega e banca em 08/10/2026. Pede:
  - plataforma funcionando de ponta a ponta (banco, backend e frontend), cobrindo no mínimo estoque e atendimento na visão interna e as telas do cliente;
  - **histórico:** responder perguntas como "qual era o estoque da loja X há duas semanas";
  - **tela inicial:** ao entrar, a pessoa já vê um resumo do que importa para ela;
  - entregáveis: repositórios organizados, estrutura do banco atualizada (incluindo os ajustes do histórico) e link de deploy acessível pela banca.
- **Repositórios:** `github.com/nicolels1/Grupo13-Backend` e `github.com/nicolels1/Grupo13-Frontend`
- **Artefatos do projeto:**
  - Diagrama do banco: `Fluxo_final_casa_lorenzi.drawio`
  - **Este `.md` é o único documento de referência do case** (contexto, decisões, regras e modelo de dados).
  - Termos em `CONTEXT.md`; decisões difíceis de reverter em `docs/adr/`
  - Tipos e restrições aplicados no banco ficam nos models (`src/models/`) e nas migrations do backend

---

## 2. Stack e arquitetura

| Camada | Escolha |
| --- | --- |
| Banco | PostgreSQL no Supabase |
| Autenticação | Supabase Auth |
| Arquivos | Supabase Storage |
| Backend | FastAPI + SQLAlchemy, migrations com Alembic |
| Frontend | React + Tailwind, com shadcn/radix |
| Deploy | Vercel (frontend) e Render (backend) |

- **Fuso:** todas as datas gravadas em `timestamptz` e exibidas no horário de Brasília (`America/Sao_Paulo`).

**Evolução:** o plano inicial era autenticação manual no backend e hospedagem do banco indefinida. Em out/2026 a arquitetura passou para Supabase (Postgres + Auth + Storage). Todo acesso a dados passa pelo FastAPI.

### Segurança e infraestrutura

- Todas as tabelas com RLS ligado e sem regra de acesso para os papéis públicos do Supabase: nada sai pela API automática.
- **Duas camadas de proteção:**
  - **Triggers no banco** (já implementados): recusam editar movimentações, históricos e o saldo do estoque direto, e garantem as regras do Admin (inclusive Gestão só no Admin), do CD, do estorno e do item do chamado. Valem para qualquer caminho, inclusive o painel do Supabase (ADRs 0009 e 0010).
  - **Usuário de banco restrito para o FastAPI** (`api_casalorenzi`, migration `56799f788354`): não pode apagar dados, exceto endereços salvos do cliente e as duas tabelas de ligação da Gestão (`modelo_permissao` e `usuario_permissao_excecao`: tirar uma permissão de um modelo ou remover uma exceção apaga a linha que liga um ao outro). Também não edita movimentações, histórico de chamados e histórico de preço, não altera o saldo do estoque direto e só lê a lista de permissões. Uma regra de RLS em cada tabela libera só ele. Ele não lê o `auth.users`: duas funções do banco respondem só o que a Gestão precisa (se um e-mail já tem login e se o login foi confirmado). As migrations continuam com o usuário completo. O trigger que atualiza o saldo roda como `SECURITY DEFINER`.
- **Situação em 06/10/2026:** os triggers do saldo e da Gestão, o `SECURITY DEFINER` e o usuário restrito estão aplicados no banco, e a API local já conecta como `api_casalorenzi`. O script `scripts/conferir_banco.py` confere, no banco de verdade, que as garantias valem (todas passaram em 06/10/2026). A migration `03aeb347f6cf` (prazos e Storage) está aplicada; falta a `bc431a82c8bf` (CPF na nota, ver seção 10).
- **Prazos automáticos:** a função `cancela_vencidos()` do banco cancela reservas vencidas (15 min) e retiradas vencidas (7 dias), e o pg_cron do Supabase a roda a cada minuto, mesmo com a API dormindo (ADR 0012).
- A chave de serviço do Supabase fica só no backend.
- FastAPI conecta pelo pooler; migrations do Alembic usam conexão direta.
- Nenhuma chamada externa (pagamento, e-mail, Storage) dentro de transação de banco.
- Ao baixar várias peças, o estoque é travado sempre na mesma ordem (evita deadlock).
- Fotos de produto em área pública do Storage (bucket `produtos`); anexos de chamado (`anexos`) e fotos de avaliação (`avaliacoes`) em área privada, com link temporário. Só o backend envia arquivos, conferindo tipo, conteúdo e tamanho; o banco guarda o caminho, nunca a URL.
- E-mails do Auth saem por servidor de e-mail próprio (o padrão do Supabase envia pouquíssimos por hora).
- **Deploy:** o plano gratuito do Render desliga o backend após 15 minutos sem tráfego; o site é aberto antes da banca para acordá-lo.

---

## 3. Escopo

- Duas plataformas com **entrada única**: interna (time da Casa Lorenzi) e do cliente.
- A plataforma também **vende**: o cliente compra e abre chamado pelo mesmo lugar.
- Vendas online e físicas são registradas na plataforma.
- Rede com lojas e centros de distribuição (CD).
- Sistema de avaliações de pedidos/produtos no estilo Shein.
- **Fora do escopo da Entrega 1:** integração com a Vulto, promoções e resposta pública da loja a avaliações.

---

## 4. Histórico de decisões

### Primeira versão do modelo (set/2026)

- PRODUTO separado de VARIANTE (cor/tamanho, SKU na variante); preço na variante, sem variar por loja.
- USUARIO dividido em base + FUNCIONARIO + CLIENTE → **revisado** para uma tabela USUARIO única.
- ESTOQUE com chave composta, sem id próprio.
- MOVIMENTACAO_ESTOQUE e MENSAGEM append-only.
- Tipos de movimentação iniciais: recebimento, dano, furto, ajuste → **revisados** (ver regras de estoque).
- Três tipos de usuário (cliente, funcionário, admin) → **revisado** para modelos de acesso.
- Tabela LOJA renomeada para **UNIDADE**.

### Revisão do modelo (out/2026)

- Toda alteração de estoque gera movimentação, inclusive venda (antes eram só alterações manuais).
- Transferência entre unidades com etapas: solicitada → enviada → recebida.
- Vendas físicas registradas por funcionário.
- Por sugestão do mentor, adotado o **centro de distribuição**: unidades são loja ou CD; o CD despacha primeiro e lojas marcadas como despachantes servem de reserva; retirada na loja escolhida pelo cliente.
- Todo cliente precisa de conta para abrir chamado, tenha comprado online ou na loja.
- Suporte a novas categorias de usuário: **modelos de acesso** (ex.: estoquista, atendente) com exceções por pessoa; admin sempre acima.
- `id_unidade` do usuário é informativo e não limita a operação.

### Revisão técnica final (out/2026)

- Estoque de cada loja separado por **canal** (loja física e online), com realocação entre canais; CD só online.
- **Reserva de estoque** no checkout online por 15 minutos.
- **Estorno** como registro próprio (permite estorno parcial).
- Ativação de conta criada no caixa exige confirmar o CPF → **revisado**: o caixa não cria mais conta (ADR 0014).
- Pedido devolvido continua **entregue**, com indicação de devolução.
- Transferência pode ser cancelada antes do envio.

### Entrega 2 — histórico e tela inicial (out/2026)

- Histórico de estoque calculado somando as movimentações até a data e hora pedidas, **sem snapshot**.
- Carga inicial do estoque como movimentação do tipo `saldo_inicial`.
- Saldo de ESTOQUE atualizado por **trigger** a cada movimentação (criando a linha se não existir), com saldo nunca negativo. O backend só insere movimentações e não tem permissão para alterar o saldo.
- Uma consulta de conferência (divergência de estoque, rota `GET /estoque/divergencias`) prova que saldo e movimentações batem; o script `scripts/conferir_banco.py` a roda no banco de verdade e confere que fica vazia.
- Peças em trânsito aparecem separadas na consulta da rede.
- Histórico de preço em tabela própria (HISTORICO_PRECO), registrado também na criação da variante.
- Pedido ganha `pago_em` e `enviado_em`.
- Reservas ficam fora do histórico, por escolha.
- Página Estoque: seletor "ver estoque em" (data com hora opcional; sem hora, fim do dia) e gráfico de evolução por canal, com períodos prontos e intervalo livre.
- A Visão Geral vira a tela inicial personalizada pelas permissões (seção 8).

### Revisão do Matias no modelo (05/10/2026)

- "Preparar pedido" e "entregar pedido na loja" viraram uma permissão só (`preparar_entregar_pedido`); o sistema tem 15 códigos de permissão.
- As permissões da Gestão são só do Admin, sem exceção, garantido por trigger.
- Lista de valores aceitos para cada campo de status e tipo, garantida por `CHECK` no banco.
- Triggers de proteção no banco (ADRs 0009 e 0010) e ADR 0008 para o cadastro feito pelo backend.

### Implementação de vendas, avaliações e arquivos (06/10/2026)

- **Frete** da entrega em casa: R$ 19,90, grátis a partir de R$ 299,00 em itens. O cliente vê o frete no carrinho, antes de a unidade ser escolhida no checkout.
- **Pagamento online** por gateway simulado: a cobrança nasce pendente e uma rota aprova ou recusa (ADR 0011).
- **Prazos** da reserva e da retirada cancelados pelo banco, com pg_cron (ADR 0012).
- **Link de ativação** da conta do caixa gerado sem e-mail e entregue pela loja ao cliente (ADR 0013) → **revisado** no dia seguinte (ADR 0014).
- **Troca** por outra cor ou tamanho do mesmo produto, sem estorno; devolução com estorno escolhido pelo atendente.
- **Denúncia procedente** oculta a avaliação com o motivo da denúncia (ou outro informado pela moderação).

### CPF na nota (07/10/2026)

- O caixa não cria mais conta. O CPF continua opcional: com conta, liga o pedido a ela; sem conta, fica no pedido como **CPF na nota** (`pedido.cpf_nota`).
- Ao criar a conta pelo site com esse CPF, as compras da loja passam para ela, sem código de confirmação. O mesmo vale na correção de CPF feita na loja.
- Saem a conta do caixa, o link de ativação e a rota de ativação (ADR 0014 substitui a 0013).
- Login por CPF com mensagem única ("CPF ou senha incorretos"), sem revelar se o CPF tem conta.
- A notinha traz só o código da venda.

### Decisões-chave e alternativas descartadas

| Decisão | Alternativa descartada | Por quê |
| --- | --- | --- |
| Toda alteração de estoque gera movimentação | Registrar só ajustes manuais | O histórico explica sempre por que a quantidade mudou |
| Transferência entre unidades com etapas | Mover estoque direto, sem registro | As lojas passam a conversar e a peça em trânsito não some |
| CD como tipo de unidade | Tabela separada para CD | Um campo resolve; a transferência vira o fluxo normal de abastecimento |
| Estoque por canal em cada loja | Saldo único; só o CD vender online | A vitrine não perde peça para o online (ADR 0004) |
| Reserva no checkout por 15 min | Conferir estoque só na aprovação e estornar se faltar | O Pix chega pago e não pode ser recusado (ADR 0002) |
| Estorno como lançamento próprio | Marcar o pagamento inteiro como estornado | Permite estorno parcial e mantém o histórico (ADR 0003) |
| Login pelo Supabase Auth, permissões no banco | Senha e tokens em tabelas próprias | Não duplica o Auth; metadados do Auth são editáveis pelo usuário (ADR 0001) |
| Modelos de acesso com exceções | Perfis fixos | Permite novas categorias sem mudar o banco |
| Uma tabela de usuários | Tabelas separadas cliente/funcionário | Mensagens e histórico têm autores dos dois lados |
| Avaliação ligada ao item do pedido | Qualquer cliente avaliar o produto | Só quem comprou avalia, uma vez por compra |
| Pedido devolvido continua entregue | Novo status "devolvido" | O status não perde a informação de entrega |
| Ativação do caixa exige confirmar CPF (substituída pela ADR 0014) | Aceitar risco de e-mail errado | Quem recebeu o link por engano não vê os dados do cliente |
| Ativação sem limite de tentativas, só com o prazo do link (substituída pela ADR 0014) | Bloquear a conta após 5 erros, com desbloqueio numa loja | O cliente não precisa ir à loja para destravar a conta; menos regra para construir antes da banca |
| Histórico de estoque calculado somando as movimentações | Foto diária do estoque (snapshot) | Exato até o segundo e sem uma segunda fonte que possa divergir; o volume do case não exige foto (ADR 0006) |
| Saldo atualizado por trigger, nunca negativo | Backend atualizar saldo e movimentação juntos | Nenhum fluxo consegue mudar um sem o outro, então o histórico não mente (ADR 0005) |
| Histórico de preço em tabela própria | Só o preço pago no item do pedido | Responde quanto a peça custava em qualquer data, mesmo sem venda |
| Visão Geral como tela inicial montada pelas permissões | Página "Início" separada | Não cria item novo na barra e mostra só o que importa para a conta |
| Toda conta criada pelo backend, que cria o login e a linha de usuário | Cadastro direto no Supabase Auth pelo site | As regras do cadastro ficam num lugar só e o CPF repetido tem mensagem clara (ADR 0008) |
| Regras entre tabelas garantidas por trigger (Admin, CD, estorno, item do chamado) | Validar só no backend | Valem para qualquer caminho, inclusive o painel do Supabase (ADRs 0009 e 0010) |
| Gateway de pagamento simulado pelo backend | Integrar o sandbox de um gateway real | O fluxo da venda fica completo sem conta, chaves nem webhook externos; trocar depois muda só quem aprova (ADR 0011) |
| Prazos cancelados pelo banco com pg_cron | Cancelar só quando alguém consulta | Vale mesmo com a API dormindo no Render e não deixa peça presa na reserva (ADR 0012) |
| CPF na nota sem conta; compras ligadas no cadastro pelo site | Criar conta no caixa e ativar por link | O cliente não precisa de e-mail nem de link na loja; risco aceito: quem cadastrar o CPF de outra pessoa vê as compras dela, sem poder trocar nem devolver (ADR 0014) |
| Frete fixo, grátis a partir de um valor | Frete por distância | O frete não muda entre o carrinho e o checkout, quando a unidade é escolhida |

---

## 5. Regras de negócio

### Catálogo

- Produto tem variantes de cor e tamanho, únicas por produto. Peça sem cor ou tamanho usa "Única" e "U".
- O preço fica na variante e não varia por loja. Toda definição de preço, inclusive a da criação, vai para o histórico de preço.
- A foto pertence ao produto, com cor opcional (sem cor, vale para todas) e ordem. O banco guarda o caminho do arquivo no Storage, não a URL.

### Unidades e estoque

- Unidade é **loja** ou **CD**. O CD não atende público, não faz venda física nem retirada, e sempre despacha online. Lojas despacham só se marcadas.
- Estoque de cada variante em cada unidade dividido por canal: loja física e online. Toda loja tem os dois; o CD só o online. Quantidade nunca negativa.
- Pedido online (inclusive retirada) usa o estoque online; venda física usa o de loja física.
- **Disponível** = estoque − reservado para pedidos online aguardando pagamento. Venda, transferência e realocação só usam o disponível.
- **Toda** alteração gera movimentação, nunca editada nem apagada. Quantidade com sinal (positiva entra, negativa sai). Tipos: saldo inicial, recebimento, avaria, perda, ajuste, venda, retorno por cancelamento, devolução, saída por troca, saída e entrada por transferência, saída e entrada por realocação.
- Motivo obrigatório em avaria, perda e ajuste. Movimentações automáticas apontam para a origem (pedido, transferência ou chamado) e podem não ter autor.
- O saldo em ESTOQUE é atualizado pelo banco (trigger) a cada movimentação inserida; o trigger cria a linha quando ela não existe. O backend nunca altera o saldo direto.
- **Realocação:** passa peças disponíveis de um canal para o outro na mesma unidade, com saída e entrada registradas juntas. Usa a permissão de movimentar estoque.
- Estoque mínimo por variante, unidade e canal. Abaixo dele, alerta de reposição; sem mínimo, lista de pendências.
- Mercadoria entra preferencialmente pelo CD e abastece as lojas por transferência. No recebimento, quem registra escolhe o canal. Reabastecimento manual.

### Transferência

- Etapas: solicitada → enviada → recebida, com várias variantes por transferência. A solicitação não reserva estoque na origem.
- Cada item informa canal de saída (sempre online quando sai do CD) e canal de entrada.
- Envio gera saída na origem; recebimento gera entrada no destino. Origem ≠ destino.
- Peças que não chegam: o destino registra a entrada do total enviado e, em seguida, a perda/avaria da diferença, com motivo.
- Antes do envio, origem ou destino podem cancelar com motivo. Depois de enviada, não se cancela.

### Vendas e pedidos

- Toda venda vira pedido, com canal online ou loja física, saindo de uma única unidade.
- **Venda física:** registrada por funcionário (gravado no pedido). Nasce entregue, com pagamento aprovado e baixa do estoque de loja física no ato. A notinha traz o código da venda.
- **Venda online:** exige conta; modalidade entrega em casa ou retirada. Pagamento sempre online. O checkout reserva as peças no estoque online por 15 minutos; o estoque baixa quando o pagamento é aprovado. Reserva vencida cancela o pedido. A cobrança Pix expira junto, e pagamento tardio é estornado automaticamente.
- **Entrega em casa:** sai de um CD com todos os itens disponíveis no online; se nenhum tiver, de uma loja que despacha, priorizando mesma cidade, depois mesmo estado, desempate pelo maior estoque. O pedido guarda cópia do endereço.
- **Retirada:** o cliente escolhe uma loja com todos os itens no estoque online e tem 7 dias para retirar; senão, cancelamento com estorno e retorno ao estoque. Na retirada, mostra o código do pedido e um documento.
- Status: aguardando pagamento → pago → enviado ou pronto para retirada → entregue; ou cancelado. O cliente cancela até o pedido estar pago.
- O pedido registra quando foi pago, enviado, ficou pronto para retirada, foi entregue ou cancelado.
- Cancelamento sempre com motivo: cliente, reserva vencida, retirada vencida ou equipe (registra quem e a justificativa).
- Após devolução, o pedido continua entregue e indica devolução parcial ou total. Troca não conta como devolução.
- Valor total = itens + frete; frete zero na retirada e na venda física. Na entrega em casa, R$ 19,90, grátis a partir de R$ 299,00 em itens. O item guarda o preço do momento da compra.
- Pagamento online pelo gateway simulado: a cobrança nasce pendente e é aprovada ou recusada; recusada, o cliente tenta de novo enquanto a reserva vale (ADR 0011).
- Vários pagamentos por pedido; fica pago quando aprovados − estornos aprovados ≥ total. Métodos: Pix, crédito, débito e dinheiro (só loja física).
- **Estorno:** lançamento próprio, ligado ao pagamento original e, em troca/devolução, ao chamado. Pode ser parcial e volta pelo mesmo método. Com vários pagamentos, o atendente escolhe de qual sai.

### Clientes e contas

- Uma tabela de usuários com tipo **interna** ou **cliente**; tela de entrada única decide o destino pelo tipo de conta.
- O Supabase Auth guarda e-mail, senha, links e sessão. A tabela de usuários usa o mesmo id do Auth e guarda tipo de conta, CPF, modelo de acesso e status. Tipo de conta e permissões nunca ficam no Auth.
- Toda conta é criada pelo backend; cadastro aberto no Auth desligado.
- O cliente que se cadastra pelo site já entra com o e-mail confirmado, sem link de confirmação: evita depender do envio de e-mails antes da banca. O risco aceito é um e-mail digitado errado só aparecer depois, corrigido numa loja.
- Cliente tem CPF único e obrigatório e entra com e-mail ou CPF (login por CPF feito pelo backend, sem expor o e-mail). Funcionário entra só com e-mail corporativo e usa conta pessoal para comprar.
- Admin cria conta interna sem senha, por convite do Auth. Recuperação de senha pelo Auth. Links valem até 24h e podem ser reenviados.
- Enquanto o servidor de e-mail próprio não estiver configurado, o convite não chega a quem não é da equipe do projeto Supabase. Por isso o Admin também pode criar a conta interna com uma **senha provisória**, que a pessoa troca depois; sem senha provisória, vale o convite.
- Desativar uma conta bloqueia o login no Auth.
- **Na loja física:** o vendedor pede o CPF (sem exigir) e o caixa nunca cria conta. CPF de quem tem conta liga o pedido a ela; CPF sem conta fica no pedido como **CPF na nota** (ADR 0014).
- Ao criar a conta pelo site, as compras com aquele CPF na nota e ainda sem cliente passam para a conta nova, sem código de confirmação. O mesmo acontece quando uma correção de CPF feita na loja passa a apontar para aquele CPF. Risco aceito: quem cadastrar primeiro o CPF de outra pessoa vê as compras dela na loja, mas não troca nem devolve (exige peça e documento); o dono corrige o CPF em qualquer loja.
- Quem comprou sem CPF pode **reivindicar** a compra pelo código da venda no comprovante, uma única vez. Sem o código, não há troca nem devolução.
- O login por CPF responde a mesma mensagem ("CPF ou senha incorretos") para CPF sem conta e senha errada, com "Criar conta" sempre visível.
- E-mail ou CPF errados são corrigidos em qualquer loja, com documento. Se o CPF certo já tiver conta, os pedidos passam para ela e a errada é desativada.
- Cliente pode ter vários endereços salvos e apagá-los (o pedido guarda sua cópia).

### Atendimento

- Só abre chamado quem tem conta. O chamado pode apontar para um pedido do próprio cliente, um item, uma variante (dúvida sem compra), uma unidade ou um chamado anterior.
- Status: aberto, em andamento, concluído, sem reabertura. Motivo de conclusão: resolvido, desistência ou sem resposta.
- Fila geral: quem tem permissão assume, define prioridade e pode repassar. Chamado assumido não pode ser assumido por outro ao mesmo tempo. Mudanças de status, responsável ou prioridade vão para o histórico, nunca editado.
- Mensagens podem ser internas (invisíveis ao cliente). Toda mensagem tem texto ou anexo; anexo em área privada do Storage.
- Troca e devolução: até 30 dias após a entrega, em qualquer loja. A peça devolvida entra no estoque de loja física daquela loja, e a peça nova da troca sai dele. Estorno ligado ao chamado.

### Avaliações

- Só quem recebeu a peça avalia: uma avaliação por item de pedido entregue, nota 1 a 5, texto opcional, até 5 fotos. Compra física só depois de estar na conta (pelo CPF na nota ou pela reivindicação).
- Publicada na hora; o cliente edita por 7 dias e não apaga. A equipe pode ocultar, com motivo, e as fotos saem do ar.
- Outros clientes votam "Útil" (um voto cada) e podem denunciar uma vez por avaliação. Ninguém vota nem denuncia a própria.

### Histórico

- **Histórico de estoque:** como estava o estoque de uma variante numa unidade ao longo do tempo, calculado somando as movimentações até a data e hora pedidas.
- Na consulta da rede inteira, peças de transferências enviadas e ainda não recebidas naquela data aparecem separadas, como em trânsito.
- **Histórico de preço:** o preço de uma variante em qualquer data, registrado na criação e a cada mudança; nunca editado.
- Reservas não entram no histórico (duram até 15 minutos e não mudam a quantidade). O histórico responde quanto havia, não quanto estava disponível.
- Datas sem hora significam o fim do dia no horário de Brasília.
- **Na tela (página Estoque):**
  - seletor "ver estoque em": data com hora opcional, mostrando a tabela como estava naquele momento;
  - gráfico de evolução com uma linha por canal;
  - períodos prontos: últimas 24 horas, 7, 30 e 90 dias, mais intervalo personalizado com data e hora;
  - granularidade automática pelo tamanho do intervalo (até 2 dias por hora, até 90 dias por dia, acima disso por semana), com troca manual.

---

## 6. Permissões e modelos de acesso

Cada funcionário está ligado a um modelo de acesso e pode ter exceções individuais que acrescentam ou retiram permissões. Mudar um modelo afeta todos os ligados; as exceções continuam valendo.

| Área | Permissões | Admin | Funcionário | Estoquista | Atendente |
| --- | --- | --- | --- | --- | --- |
| Contas | gerenciar contas; gerenciar modelos de acesso | sim | — | — | — |
| Catálogo | gerenciar produtos e categorias | sim | — | — | — |
| Unidades | gerenciar unidades | sim | — | — | — |
| Estoque | movimentar estoque; definir estoque mínimo | sim | sim | sim | — |
| Transferência | solicitar; enviar; receber | sim | sim | sim | — |
| Vendas | registrar venda física; preparar e entregar pedido; cancelar pela equipe; corrigir cadastro de cliente | sim | sim | — | — |
| Atendimento | atender chamado | sim | sim | — | sim |
| Avaliações | moderar avaliações | sim | — | — | sim |

- Existe um único modelo Admin, com todas as permissões (inclusive futuras), sem aceitar retirada.
- Sempre há pelo menos uma conta ativa no Admin; modelo com pessoas ligadas não pode ser desativado.
- Permissões lidas do banco a cada ação: mudanças valem na hora.
- As três permissões da Gestão (gerenciar contas, gerenciar modelos de acesso e gerenciar unidades) são só do Admin: não entram em outro modelo nem em exceção. Exceção só vale para conta interna. As regras do Admin são garantidas por trigger no banco (ADRs 0009 e 0010).
- No sistema, cada permissão tem um código. São 15: `gerenciar_contas`, `gerenciar_modelos_acesso`, `gerenciar_catalogo`, `gerenciar_unidades`, `movimentar_estoque`, `definir_estoque_minimo`, `solicitar_transferencia`, `enviar_transferencia`, `receber_transferencia`, `registrar_venda_fisica`, `preparar_entregar_pedido`, `cancelar_pedido_equipe`, `corrigir_cadastro_cliente`, `atender_chamado`, `moderar_avaliacoes`.

---

## 7. Modelo de dados (27 tabelas)

Campos com `?` aceitam vazio. A chave primária vem primeiro. No banco, nomes ficam em minúsculas (`usuario`, `item_pedido`).

### Catálogo

| Tabela | Campos | Liga com |
| --- | --- | --- |
| CATEGORIA_PRODUTO | id_categoria, nome (único), ativo | PRODUTO (1:N) |
| PRODUTO | id_produto, id_categoria, nome, descricao_tecnica, descricao_cliente, ativo | VARIANTE, IMAGEM_PRODUTO (1:N) |
| VARIANTE | id_variante, id_produto, sku (único), cor, tamanho, preco, ativo — única por produto + cor + tamanho | ESTOQUE, ITEM_PEDIDO, ITEM_TRANSFERENCIA, HISTORICO_PRECO (1:N) |
| IMAGEM_PRODUTO | id_imagem, id_produto, cor?, caminho_arquivo, ordem | PRODUTO |
| HISTORICO_PRECO | id_historico_preco, id_variante, preco_anterior?, preco_novo, id_alterado_por?, alterado_em | VARIANTE |

### Estoque e transferência

| Tabela | Campos | Liga com |
| --- | --- | --- |
| UNIDADE | id_unidade, nome (único), tipo, despacha_online, rua, numero, complemento?, bairro, cidade, uf, cep, ativo | ESTOQUE, PEDIDO, TRANSFERENCIA (1:N) |
| ESTOQUE | id_variante + id_unidade + canal, quantidade, quantidade_reservada, estoque_minimo?, minimo_alterado_por?, minimo_alterado_em?, atualizado_em | MOVIMENTACAO_ESTOQUE (1:N) |
| MOVIMENTACAO_ESTOQUE | id_movimentacao, id_variante, id_unidade, canal, id_usuario?, tipo, quantidade, motivo?, id_pedido?, id_transferencia?, id_chamado?, criado_em | no máximo uma origem |
| TRANSFERENCIA | id_transferencia, id_unidade_origem, id_unidade_destino, status, id_solicitante, id_enviado_por?, id_recebido_por?, id_cancelado_por?, motivo_cancelamento?, solicitada_em, enviada_em?, recebida_em?, cancelada_em? | ITEM_TRANSFERENCIA (1:N) |
| ITEM_TRANSFERENCIA | id_item_transferencia, id_transferencia, id_variante, canal_saida, canal_entrada, quantidade_solicitada, quantidade_enviada?, quantidade_recebida? | TRANSFERENCIA, VARIANTE |

### Vendas

| Tabela | Campos | Liga com |
| --- | --- | --- |
| PEDIDO | id_pedido, codigo_venda (único), id_cliente?, id_unidade, id_registrado_por?, cpf_nota?, canal, modalidade?, status, motivo_cancelamento?, id_cancelado_por?, justificativa_cancelamento?, devolucao, valor_frete, valor_total, pronto_retirada_em?, reserva_expira_em?, pago_em?, enviado_em?, entregue_em?, cancelado_em?, criado_em, atualizado_em | ITEM_PEDIDO, PAGAMENTO (1:N); ENDERECO_ENTREGA (1:0..1) |
| ITEM_PEDIDO | id_item, id_pedido, id_variante, quantidade, preco_unitario | AVALIACAO (1:0..1) |
| PAGAMENTO | id_pagamento, id_pedido, tipo, id_pagamento_original?, id_chamado?, metodo, id_transacao_gateway? (único), valor, status, criado_em, atualizado_em | PEDIDO; PAGAMENTO (estorno → original); CHAMADO |
| ENDERECO_ENTREGA | id_pedido, rua, numero, complemento?, bairro, cidade, uf, cep | PEDIDO (só na entrega) |
| ENDERECO_CLIENTE | id_endereco, id_cliente, rua, numero, complemento?, bairro, cidade, uf, cep | USUARIO |

### Contas e acesso

| Tabela | Campos | Liga com |
| --- | --- | --- |
| USUARIO | id_usuario (= id do Supabase Auth), nome, email (único, cópia do Auth), cpf? (único; obrigatório para cliente), tipo_conta, status_conta, id_modelo_acesso?, id_unidade?, criado_em, atualizado_em | auth.users (1:1); PEDIDO, CHAMADO, MENSAGEM (1:N) |
| MODELO_ACESSO | id_modelo, nome (único), eh_admin, ativo | USUARIO (1:N) |
| PERMISSAO | id_permissao, codigo (único), descricao | lista fixa do sistema |
| MODELO_PERMISSAO | id_modelo + id_permissao | modelo ↔ permissão (N:N) |
| USUARIO_PERMISSAO_EXCECAO | id_usuario + id_permissao, efeito | exceções individuais |

### Atendimento

| Tabela | Campos | Liga com |
| --- | --- | --- |
| CHAMADO | id_chamado, id_cliente, id_responsavel?, id_unidade?, id_pedido?, id_item_pedido?, id_variante?, id_chamado_anterior?, categoria, assunto, descricao, status, prioridade?, motivo_encerramento?, criado_em, atualizado_em, assumido_em?, concluido_em? | MENSAGEM, HISTORICO_CHAMADO, PAGAMENTO (1:N) |
| MENSAGEM | id_mensagem, id_chamado, id_autor, conteudo?, anexo_caminho?, anexo_nome?, anexo_tamanho?, interna, lida_em?, criado_em | CHAMADO, USUARIO |
| HISTORICO_CHAMADO | id_historico, id_chamado, id_autor, campo_alterado, valor_anterior?, valor_novo, criado_em | CHAMADO |

### Avaliações

| Tabela | Campos | Liga com |
| --- | --- | --- |
| AVALIACAO | id_avaliacao, id_item_pedido (único), nota, texto?, status, motivo_ocultacao?, id_ocultada_por?, ocultada_em?, criada_em, editada_em? | FOTO_AVALIACAO, VOTO_UTIL, DENUNCIA_AVALIACAO (1:N) |
| FOTO_AVALIACAO | id_foto, id_avaliacao, caminho_arquivo, ordem (1 a 5) | AVALIACAO |
| VOTO_UTIL | id_avaliacao + id_cliente, criado_em | AVALIACAO, USUARIO |
| DENUNCIA_AVALIACAO | id_denuncia, id_avaliacao, id_cliente, motivo, status, id_analisada_por?, analisada_em?, criada_em — uma por cliente por avaliação | AVALIACAO, USUARIO |

Campos de "quem fez" (minimo_alterado_por, id_registrado_por, id_cancelado_por, id_analisada_por, id_alterado_por etc.) apontam para USUARIO, mas não têm linha no diagrama para não poluí-lo. `auth.users` aparece só como referência.

### Valores aceitos

Campos de status e de tipo só aceitam os valores abaixo, sempre em minúsculas e com `_` no lugar de espaço. O banco recusa qualquer outro valor (restrição `CHECK`).

| Campo | Valores aceitos |
| --- | --- |
| unidade.tipo | loja, cd |
| canal (estoque, movimentação, pedido, item de transferência) | loja_fisica, online |
| movimentacao_estoque.tipo | saldo_inicial, recebimento, avaria, perda, ajuste, venda, retorno_cancelamento, devolucao, saida_troca, saida_transferencia, entrada_transferencia, saida_realocacao, entrada_realocacao |
| transferencia.status | solicitada, enviada, recebida, cancelada |
| pedido.status | aguardando_pagamento, pago, enviado, pronto_para_retirada, entregue, cancelado |
| pedido.modalidade | entrega, retirada (só na venda online) |
| pedido.motivo_cancelamento | cliente, reserva_vencida, retirada_vencida, equipe |
| pedido.devolucao | nenhuma, parcial, total |
| pagamento.tipo | pagamento, estorno |
| pagamento.metodo | pix, cartao_credito, cartao_debito, dinheiro |
| pagamento.status | pendente, aprovado, recusado |
| usuario.tipo_conta | interna, cliente |
| usuario.status_conta | pendente_ativacao, ativa, inativa |
| usuario_permissao_excecao.efeito | acrescentar, retirar |
| chamado.categoria | entrega, troca_devolucao, estorno, duvida, outros |
| chamado.status | aberto, em_andamento, concluido |
| chamado.prioridade | baixa, media, alta |
| chamado.motivo_encerramento | resolvido, desistencia, sem_resposta |
| avaliacao.status | publicada, oculta |
| denuncia_avaliacao.status | pendente, procedente, improcedente |

### Como ler o diagrama

Cada linha liga duas tabelas uma única vez. A ponta encostada na tabela dá o máximo; a mais afastada, o mínimo.

| Ponta | Significa | Exemplo |
| --- | --- | --- |
| Dois traços | exatamente um | cada item pertence a exatamente um pedido |
| Traço com bolinha | zero ou um | um item tem no máximo uma avaliação |
| Pé de galinha com bolinha | zero ou muitos | um pedido tem zero ou muitos pagamentos |

Cores dos cabeçalhos: amarelo = catálogo, laranja = estoque, laranja-escuro = vendas, vermelho-escuro = contas, vinho = atendimento, roxo = avaliações.

---

## 8. Plataforma interna e design

### Navegação

- Barra no topo (não sidebar): **Visão Geral, Estoque, Transferências, Pedidos, Atendimento, Avaliações, Catálogo e Gestão**.
- **Gestão** (Unidades + Usuários e acessos) é exclusiva do admin, sem exceção.
- O que o funcionário não tem permissão nem aparece na tela dele.
- Catálogo e unidades fazem parte da plataforma.

### Visão Geral = tela inicial (revisada na Entrega 2)

- É a primeira tela ao entrar e mostra **só o que faz sentido para as permissões da conta**. Sem subabas: uma página só.
- **No topo, "Precisa da sua atenção":** blocos de pendência, cada um com um número e um atalho para a página que resolve. Bloco zerado aparece como "tudo em dia", em vez de sumir.

| Permissão | Bloco |
| --- | --- |
| atender chamado | chamados sem responsável; meus chamados em andamento; chamados com mensagem nova do cliente |
| movimentar estoque | variantes abaixo do mínimo; transferências chegando para minha unidade; transferências para eu enviar |
| preparar e entregar pedido | pedidos pagos para preparar; retiradas prontas há mais de 5 dias (risco de vencer) |
| moderar avaliações | denúncias pendentes |

- **Abaixo:** indicadores de cada área (estoque, atendimento etc.), também só das áreas que a pessoa tem permissão.
- **Unidade:** abre filtrada pela unidade da pessoa; o seletor permite trocar para "Todas". Quem não tem unidade abre em "Todas". Não muda nenhuma regra de permissão, só o filtro inicial.
- Com "Todas as unidades", as tabelas mostram a coluna Unidade; com uma unidade selecionada, a coluna some.
- A Visão Geral identifica; as ações acontecem nas páginas de cada área. Não há configuração de estoque mínimo nela.

### Identidade visual

- Paleta da plataforma integrada (Lorenzi + Vulto) em tons de vermelho, de preferência com degradê.
- Estilização feita por etapas, começando pela dashboard; cores ficaram para uma fase posterior.

---

## 9. Em aberto

### Integração com a Vulto (próximas entregas)

O modelo já foi pensado para não travar nela:

- Funcionários da Vulto entram como contas internas comuns (a entrada decide pelo tipo de conta, não pelo domínio do e-mail).
- Lojas e CDs da Vulto entram como novas unidades.
- **A decidir:** incluir campo de marca/empresa em UNIDADE, PRODUTO e talvez USUARIO; como unir clientes que existam nas duas bases.

### Evoluções futuras (depois da Entrega 2)

- Reabastecimento automático (sugerir transferência do CD quando a loja fica abaixo do mínimo) — já suportado pelo modelo.
- Completar uma retirada por transferência — exigiria ligar pedido a um envio.
- Troca com envio para casa — exigiria ligar chamado a um envio.

---

## 10. Pendências — precisam ser resolvidas

Cada uma precisa estar resolvida antes da banca (08/10/2026).

| # | Pendência | O que falta | Situação |
| --- | --- | --- | --- |
| 1 | Usuário de banco restrito para o FastAPI | Migration `56799f788354` aplicada e senha definida; a API local já conecta com ele. Conferir se a `DATABASE_URL` do Render também usa `api_casalorenzi` | ⚠️ Conferir o Render |
| 2 | Trigger do saldo compatível com o usuário restrito | `scripts/conferir_banco.py` confirmou em 06/10/2026 que a movimentação atualiza o saldo e que o saldo não muda direto | ✅ Resolvido |
| 3 | Conferência de divergência de estoque | Rota `GET /estoque/divergencias`; `scripts/conferir_banco.py` confirmou em 06/10/2026 que ela fica vazia | ✅ Resolvido |
| 4 | Vendas, avaliações e arquivos no banco | Migration `03aeb347f6cf` aplicada (função dos prazos, pg_cron e buckets) e `scripts/conferir_banco.py` rodado | ✅ Resolvido |
| 5 | Variáveis do Render | `SUPABASE_SERVICE_ROLE_KEY` e `SUPABASE_PUBLISHABLE_KEY` configuradas; o login por CPF em produção responde como esperado | ✅ Resolvido |
| 6 | CPF na nota no banco | Aplicar a migration `bc431a82c8bf` (`alembic upgrade head`): cria a coluna `pedido.cpf_nota`. Sem ela, as rotas de pedidos, vendas, avaliações e o cadastro pelo site falham em produção | ⚠️ Código pronto — falta aplicar antes do merge |
