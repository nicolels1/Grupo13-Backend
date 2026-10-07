# Gateway de pagamento simulado

**Contexto:** a venda online cobra por Pix e cartão, mas o case não tem conta em gateway de pagamento. Integrar um sandbox real (Mercado Pago, Stripe) exigiria conta, chaves, webhook público e mais tempo do que a Entrega 2 permite.

**Decisão:** o backend faz o papel do gateway. A cobrança nasce `pendente`, com um id de transação `sim_...`, e a rota `POST /pagamentos/{id}/simular` aprova ou recusa. Todo o resto é real: a reserva de 15 minutos, a baixa do estoque na aprovação, o estorno automático de pagamento que chega depois do cancelamento e os estornos parciais pelo atendimento, que o gateway simulado aprova na hora.

**Por quê:** o fluxo da venda fica completo e testável sem depender de serviço externo. Trocar pelo gateway real muda só quem chama a aprovação (um webhook em vez da rota de simulação) e onde o estorno é pedido; as tabelas e as regras do pedido continuam as mesmas.
