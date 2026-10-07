# CPF na nota sem conta; compras ligadas pelo CPF no cadastro

Substitui a ADR 0013 e, na ADR 0008, a conta criada no caixa: o caixa não cria mais conta.

**Contexto:** na venda física, a regra anterior criava no caixa uma conta sem senha, ativada depois por um link com confirmação do CPF. Isso exigia e-mail para o cliente, link de ativação, uma página só para ativar e uma conta que existia sem que o cliente tivesse pedido. Na loja, o que o cliente costuma dar é só o CPF na nota.

**Decisão:** o CPF continua opcional na venda física e o caixa nunca cria conta. CPF de quem já tem conta liga o pedido a ela. CPF sem conta fica guardado no pedido (`cpf_nota`). Quando alguém cria a conta pelo site com esse CPF, todas as compras com ele na nota e ainda sem cliente passam para a conta nova, sem código de confirmação. O mesmo vale quando uma correção de CPF feita na loja passa a apontar para aquele CPF. Quem comprou sem CPF continua podendo reivindicar a compra pelo código da venda. O login por CPF responde a mesma mensagem ("CPF ou senha incorretos") para CPF sem conta e senha errada.

**Por quê:** o cliente não precisa de e-mail nem de link na loja, e a compra aparece sozinha quando ele cria a conta. O código fica menor: saem a conta do caixa, o link e a ativação.

**Risco aceito:** quem souber o CPF de outra pessoa e se cadastrar primeiro com ele vê as compras dela feitas em lojas. Essa pessoa não consegue trocar nem devolver, porque isso exige a peça e um documento na loja. O dono real corrige o CPF em qualquer loja, com documento: a conta errada é desativada e as compras passam para a conta certa (case, seção 5).
