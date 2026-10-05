# Reserva de estoque por 15 minutos no checkout online

**Contexto:** na venda online, entre o checkout e a aprovação do pagamento outra pessoa pode comprar a mesma peça. A alternativa era conferir o estoque só na aprovação e estornar se faltasse.

**Decisão:** o checkout reserva as peças no estoque online por 15 minutos. O estoque só baixa quando o pagamento é aprovado. Reserva vencida cancela o pedido, a cobrança Pix expira junto e pagamento tardio é estornado automaticamente.

**Por quê:** o Pix chega já pago e não pode ser recusado; conferir só na aprovação geraria venda sem peça e estorno frequente. A reserva garante a peça durante o pagamento sem mexer na quantidade, por isso fica fora do histórico de estoque.
