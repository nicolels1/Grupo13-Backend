# Admin sem exceções de permissão, garantido por trigger

Substitui o ADR 0007. **Atualizado pelo ADR 0010:** o Admin passa a recusar só exceção de retirada.

**Contexto:** o Admin tem todas as permissões, inclusive futuras, e não aceita exceções. O ADR 0007 deixava a regra só no backend, mas a divisão de tarefas da Entrega 2 define que regras entre tabelas ficam em trigger. A regra pode ser quebrada por três caminhos: criar uma exceção para quem já é Admin, trocar para o modelo Admin uma pessoa que já tem exceções, ou marcar um modelo como admin.

**Decisão:** três triggers no banco cobrem os três caminhos. Inserir ou alterar uma exceção de quem está no modelo Admin falha com `check_violation`. Quando uma pessoa passa para o modelo Admin, ou quando o modelo dela vira admin, as exceções dela são apagadas. A conferência de permissão no backend continua liberando o Admin sem ler exceções, e o use case de exceções valida antes de gravar, para devolver uma mensagem clara em vez do erro do banco.

**Por quê:** a regra vale para qualquer caminho, inclusive um `INSERT` feito pelo painel do Supabase ou por um fluxo que esqueça de validar. O custo é ter regra de negócio no banco, escrita à mão na migration, como o saldo do estoque (ADR 0005).
