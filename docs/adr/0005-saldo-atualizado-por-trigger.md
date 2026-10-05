# Saldo de estoque atualizado por trigger

**Contexto:** o histórico de estoque depende de o saldo atual bater sempre com a soma das movimentações. Se o backend atualizasse saldo e movimentação juntos, qualquer fluxo que esquecesse um dos dois deixaria os números divergentes.

**Decisão:** o backend só insere movimentações. Um trigger no banco atualiza o saldo a cada movimentação, cria a linha de estoque quando ela não existe e impede saldo negativo. O usuário de banco do backend não tem permissão para alterar o saldo direto, e uma consulta de divergência, coberta por teste, confere que saldo e movimentações batem.

**Por quê:** nenhum fluxo consegue mudar o saldo sem deixar movimentação, então o histórico não mente. O custo é ter regra de negócio no banco, escrita à mão na migration.
