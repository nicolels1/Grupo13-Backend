# Estorno como lançamento próprio

**Contexto:** um pedido pode ter vários pagamentos, e troca ou devolução podem devolver só parte do valor. A alternativa era marcar o pagamento inteiro como estornado.

**Decisão:** o estorno é um registro próprio, ligado ao pagamento original e, em troca ou devolução, ao chamado. Pode ser parcial e volta pelo mesmo método. O pedido está pago quando pagamentos aprovados menos estornos aprovados cobrem o total.

**Por quê:** marcar o pagamento como estornado não permite estorno parcial e apaga o que foi pago. Com lançamento próprio, o histórico financeiro do pedido fica completo e cada estorno sabe de onde saiu e por quê.
