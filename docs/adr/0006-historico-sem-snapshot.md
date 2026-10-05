# Histórico de estoque calculado pelas movimentações, sem snapshot

**Contexto:** a Entrega 2 pede responder perguntas como "qual era o estoque da loja X há duas semanas". A alternativa comum é guardar uma foto diária do estoque.

**Decisão:** o histórico é calculado somando as movimentações até a data e hora pedidas. A carga inicial entra como movimentação do tipo saldo inicial, e reservas ficam de fora.

**Por quê:** a soma é exata até o segundo e não cria uma segunda fonte de dados que possa divergir das movimentações. O volume do case não exige foto; se o cálculo ficar lento, um snapshot pode ser adicionado depois como cache, sem mudar a fonte da verdade.
