# Cliente sem conta da migração; o histórico passa para a conta no cadastro

Estende a ADR 0014 (CPF na nota sem conta) aos clientes trazidos da Vulto (decisões P2 e J1a do registro da migração).

**Contexto:** na plataforma, cliente é uma conta no Supabase Auth com e-mail e CPF obrigatórios. Os clientes da Vulto nunca criaram conta: há pessoas com CPF e sem e-mail, compradores do site identificados só pelo e-mail e CPFs que só aparecem nos pontos, sem nome. Pedidos, chamados e pontos migrados precisam apontar para alguém, e a ADR 0014 já decidiu que não criamos conta que o cliente não pediu.

**Decisão:** uma tabela `cliente_sem_conta` guarda quem não tem conta (CPF, e-mail, nome, telefone, consentimento de mensagem e origem). Pedido e chamado ganham uma chave opcional para ele; cada um aponta para um cliente com conta ou para um sem conta, nunca os dois, e a venda online aceita qualquer um deles. O CPF é único; o e-mail é único só entre quem não tem CPF, porque há e-mails de família usados por mais de um CPF. Quando alguém cria a conta pelo site, ou corrige o CPF ou o e-mail na loja, o histórico do cliente sem conta com o mesmo CPF, ou com o mesmo e-mail e sem CPF, passa para a conta, e o cliente sem conta guarda para qual conta foi. Uma compra migrada com cliente identificado não pode ser reivindicada pelo código da venda: ela aparece na conta dele.

**Por quê:** segue a regra da ADR 0014 (o histórico aparece quando a pessoa se cadastra, sem conta criada à revelia) e não exige e-mail de quem não tem. Separar do `usuario` mantém a conta como está: sem login, senha ou permissões para quem nunca se cadastrou.

**Risco aceito:** o cadastro não confirma o e-mail, então quem souber o e-mail de um cliente sem CPF e se cadastrar primeiro vê o histórico dele, como no CPF na nota (ADR 0014). A correção é a mesma: o dono real corrige o cadastro numa loja. Sugestão futura: juntar pelo e-mail só depois de confirmar o e-mail.
