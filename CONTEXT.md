# Casa Lorenzi

Plataforma única da Casa Lorenzi (rede de varejo de moda em integração com a Vulto) para o time interno operar estoque, vendas e atendimento, e para o cliente comprar, acompanhar pedidos, abrir chamados e avaliar compras.

## Organização

**Casa Lorenzi**:
Rede tradicional de varejo de moda, dona da plataforma.

**Vulto**:
Rede jovem e omnichannel em integração com a Casa Lorenzi; ainda fora do escopo da plataforma.

**Plataforma interna**:
Parte da plataforma usada pelo time da empresa.
_Evite_: painel, backoffice, admin

**Plataforma do cliente**:
Parte da plataforma usada pelo cliente para comprar, acompanhar pedidos, abrir chamados e avaliar.
_Evite_: loja virtual, site

**Entrada única**:
Tela de login comum às duas plataformas; o tipo de conta decide para qual delas a pessoa vai.

## Catálogo

**Categoria**:
Agrupamento de produtos no catálogo.

**Produto**:
Peça do catálogo, independente de cor e tamanho.
_Evite_: item, artigo

**Variante**:
Combinação de cor e tamanho de um produto; é o que tem SKU, preço e estoque. Peça sem cor ou tamanho usa "Única" e "U".
_Evite_: SKU (como nome do conceito), modelo

**SKU**:
Código único que identifica uma variante.

**Foto do produto**:
Imagem de um produto, opcionalmente de uma cor específica, exibida numa ordem definida.

**Foto da categoria**:
Imagem opcional de uma categoria, mostrada no carrossel de categorias da página inicial da loja.

**Histórico de preço**:
Registro de todos os preços que uma variante teve, desde a criação; nunca editado.

## Unidades

**Unidade**:
Ponto físico da rede: uma loja ou um centro de distribuição.
_Evite_: loja (quando o sentido for qualquer ponto da rede), filial

**Loja**:
Unidade que atende o público, faz venda física e retirada.

**Centro de distribuição (CD)**:
Unidade que não atende o público; recebe mercadoria, abastece as lojas e despacha pedidos online.
_Evite_: depósito, armazém

**Loja despachante**:
Loja marcada para enviar pedidos de entrega em casa quando nenhum CD tem todos os itens.

## Estoque

**Canal**:
Divisão do estoque de uma unidade entre **loja física** e **online**. Toda loja tem os dois; o CD só o online.

**Estoque**:
Quantidade de uma variante numa unidade, num canal.
_Evite_: saldo (fora do contexto de conferência), inventário

**Reserva**:
Peças separadas do estoque online por até 15 minutos enquanto um pedido online aguarda pagamento.

**Disponível**:
Estoque menos o reservado; é o que pode ser vendido, transferido ou realocado.

**Movimentação**:
Registro de uma entrada ou saída de estoque, com quantidade, tipo e motivo ou origem; nunca editada nem apagada.
_Evite_: lançamento, ajuste (como termo genérico)

**Saldo inicial**:
Movimentação que registra a carga inicial de estoque de uma variante numa unidade e canal.

**Recebimento**:
Entrada de mercadoria nova numa unidade, preferencialmente pelo CD.

**Avaria**:
Saída de peças danificadas, sempre com motivo.
_Evite_: dano

**Perda**:
Saída de peças que sumiram (inclui furto), sempre com motivo.
_Evite_: furto, extravio

**Ajuste**:
Correção manual de quantidade, sempre com motivo.

**Realocação**:
Passagem de peças disponíveis de um canal para o outro dentro da mesma unidade.
_Evite_: transferência (que é entre unidades)

**Estoque mínimo**:
Quantidade de referência por variante, unidade e canal abaixo da qual a unidade precisa repor.

**Alerta de reposição**:
Aviso de que o estoque de uma variante ficou abaixo do estoque mínimo.

## Transferência

**Transferência**:
Envio de peças de uma unidade para outra, com as etapas solicitada → enviada → recebida, ou cancelada antes do envio.
_Evite_: remessa, realocação

**Peça em trânsito**:
Peça de uma transferência já enviada e ainda não recebida.

## Histórico

**Histórico de estoque**:
Quanto havia de uma variante numa unidade e canal em qualquer momento do passado, calculado a partir das movimentações. Não considera reservas.
_Evite_: snapshot, foto do estoque

**Ver estoque em**:
Consulta do estoque como estava numa data e hora escolhidas; data sem hora significa o fim do dia no horário de Brasília.

**Divergência de estoque**:
Diferença entre o estoque atual e a soma das movimentações; deve ser sempre zero.

## Vendas

**Pedido**:
Registro de uma venda, online ou física, que sai de uma única unidade.
_Evite_: compra, ordem, venda (como registro)

**Código da venda**:
Identificador do pedido impresso no comprovante e usado na retirada e na reivindicação.

**Venda física**:
Pedido registrado por um funcionário na loja; nasce pago e entregue.
_Evite_: venda de balcão, PDV

**Venda online**:
Pedido feito pelo cliente na plataforma, com pagamento online e modalidade entrega em casa ou retirada.

**Modalidade**:
Forma de receber uma venda online: **entrega em casa** ou **retirada**.

**Entrega em casa**:
Modalidade em que o pedido é enviado ao endereço do cliente a partir de um CD ou loja despachante.

**Retirada**:
Modalidade em que o cliente busca o pedido numa loja escolhida, em até 7 dias.

**Frete**:
Valor de envio somado aos itens; zero na retirada e na venda física.

**Pagamento**:
Valor pago por um método (Pix, crédito, débito ou dinheiro) para um pedido; um pedido pode ter vários.

**Cobrança**:
Pagamento online que ainda aguarda a resposta do gateway; vence junto com a reserva.

**Gateway simulado**:
Papel do gateway de pagamento feito pelo próprio backend: aprova ou recusa a cobrança (ADR 0011).
_Evite_: pagamento fake, mock

**Estorno**:
Devolução de dinheiro registrada separadamente, ligada a um pagamento original; pode ser parcial.
_Evite_: reembolso, cancelamento de pagamento

**Cancelamento**:
Encerramento de um pedido antes da entrega, sempre com motivo: cliente, reserva vencida, retirada vencida ou equipe.

**Devolução**:
Retorno de peças de um pedido entregue com estorno; o pedido continua entregue e passa a indicar devolução parcial ou total.

**Troca**:
Substituição de uma peça entregue por outra; não conta como devolução.

**CPF na nota**:
CPF informado na venda física por quem não tem conta; a compra passa para a conta criada pelo site com esse CPF.
_Evite_: CPF do cliente (quando não há conta)

**Balcão**:
Atendimento presencial na loja, sem chamado: venda física, troca e devolução registradas pela pessoa da equipe naquela loja.

**Reivindicação**:
Associação, pelo código da venda, de uma compra feita sem CPF à conta do cliente; acontece uma única vez.

## Contas e acesso

**Conta**:
Cadastro de uma pessoa na plataforma, de tipo interna ou cliente.
_Evite_: perfil, login

**Conta interna**:
Conta de quem trabalha na empresa; usa e-mail corporativo.
_Evite_: conta de funcionário, conta admin

**Cliente**:
Pessoa com conta do tipo cliente, identificada por CPF.
_Evite_: consumidor, comprador

**Funcionário**:
Pessoa com conta interna. Para comprar, usa uma conta de cliente pessoal.
_Evite_: colaborador, usuário interno

**Desativação**:
Bloqueio de uma conta ou registro sem apagá-lo.
_Evite_: exclusão

**Modelo de acesso**:
Conjunto nomeado de permissões atribuído a contas internas (ex.: Estoquista, Atendente).
_Evite_: perfil, papel, cargo, role

**Admin**:
Modelo de acesso único com todas as permissões, inclusive futuras; não aceita exceções que retirem permissões.

**Permissão**:
Ação específica que uma conta interna pode executar (ex.: movimentar estoque, atender chamado).

**Exceção de permissão**:
Permissão acrescentada ou retirada de uma pessoa específica, além do seu modelo de acesso.

## Atendimento

**Chamado**:
Pedido de ajuda aberto por um cliente com conta, opcionalmente ligado a um pedido, item, variante, unidade ou chamado anterior.
_Evite_: ticket, reclamação, solicitação

**Fila**:
Conjunto de chamados sem responsável, visível a quem tem permissão de atender.

**Responsável**:
Pessoa que assumiu um chamado; um chamado tem no máximo um por vez.

**Prioridade**:
Urgência de um chamado, definida por quem o assume.

**Mensagem interna**:
Mensagem de um chamado visível só ao time, nunca ao cliente.
_Evite_: nota, comentário

**Motivo de conclusão**:
Por que um chamado foi concluído: resolvido, desistência ou sem resposta.

**Histórico do chamado**:
Registro de cada mudança de status, responsável ou prioridade de um chamado; nunca editado.

## Avaliações

**Avaliação**:
Nota de 1 a 5, com texto e fotos opcionais, dada por quem recebeu um item de pedido; uma por item entregue.
_Evite_: review, comentário

**Voto útil**:
Marcação de um cliente indicando que a avaliação de outra pessoa ajudou; um por cliente por avaliação.
_Evite_: curtida, like

**Denúncia**:
Sinalização de uma avaliação inadequada feita por outro cliente; uma por cliente por avaliação.

**Ocultação**:
Retirada de uma avaliação do ar pela equipe, sempre com motivo.
_Evite_: exclusão, remoção

## Plataforma interna

**Visão Geral**:
Tela inicial da plataforma interna, montada a partir das permissões da conta.
_Evite_: dashboard, início, home

**Precisa da sua atenção**:
Bloco da Visão Geral que lista pendências com contagem e atalho para a página que as resolve.

**Gestão**:
Área exclusiva do Admin para unidades, contas e modelos de acesso.
