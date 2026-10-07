# Troca e devolução no balcão, sem chamado

**Contexto:** troca, devolução e estorno só aconteciam pelo Atendimento, dentro de um chamado. No balcão, isso exigia abrir um chamado para cada cliente (a equipe nem tinha rota para isso), assumir, registrar e concluir. E todo chamado precisa de um cliente com conta, então a compra feita na loja sem conta (CPF na nota ou sem CPF, ADR 0014) não tinha como ser trocada nem devolvida.

**Decisão:** troca, devolução e estorno ficam ligados ao pedido. O chamado vira opcional: obrigatório pelo Atendimento, ausente no balcão. No balcão, ficam registradas a pessoa da equipe e a loja, com a permissão nova `registrar_troca_devolucao` (16º código). O banco garante:
- toda movimentação de troca ou devolução tem o pedido e, além dele, o chamado ou a pessoa da equipe;
- todo estorno tem uma origem: `atendimento` exige o chamado; `balcao` exige a pessoa e a loja; `cancelamento` (pela equipe, retirada vencida no pg_cron ou pagamento depois da reserva vencida) não exige nenhum dos dois.

Vale para qualquer pedido entregue há no máximo 30 dias, com ou sem conta. Troca e devolução só em loja: a peça devolvida entra no estoque de loja física daquela loja e a nova sai dele. O estorno sai pelo mesmo meio de pagamento. A equipe acha o pedido pelo código da venda, pelo número do pedido ou pelo CPF (da conta ou na nota, entregues nos últimos 30 dias).

**Por quê:** a operação do balcão fica num passo só e atende quem não tem conta, sem perder o registro de quem fez e onde. Como tudo fica no pedido, o limite de peças devolvidas vale junto para o balcão e para o Atendimento.
