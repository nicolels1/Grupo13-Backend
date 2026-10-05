# Admin ignora exceções de permissão, regra garantida no backend

**Substituído pelo ADR 0009** (a regra passou a ser garantida por trigger).

**Contexto:** o Admin tem todas as permissões, inclusive futuras, e não aceita exceções que retirem permissões. A regra cruza três tabelas (exceções, usuário e modelo de acesso) e pode ser quebrada por três caminhos: criar uma exceção para quem já é Admin, trocar para o modelo Admin uma pessoa que já tem exceções, ou marcar um modelo como admin.

**Decisão:** a regra fica no backend, sem trigger. A conferência de permissão verifica primeiro se o modelo da pessoa é o Admin e, se for, libera a ação sem ler as exceções. O use case de exceções recusa qualquer exceção para uma pessoa com modelo Admin, seja para acrescentar ou para retirar. Ao trocar uma pessoa para o modelo Admin, as exceções dela são apagadas na mesma transação.

**Por quê:** como a conferência ignora as exceções do Admin, uma exceção que sobre no banco não tem efeito, então a regra vale mesmo se uma validação falhar. Cobrir os três caminhos com triggers exigiria código escrito à mão em três tabelas para evitar um dado que não causa dano, ao contrário do saldo de estoque (ADR 0005). Apagar as exceções na troca evita que exceções antigas voltem a valer se a pessoa sair do modelo Admin.
