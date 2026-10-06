# Admin recusa só exceção de retirada, como no diagrama

Substitui em parte o ADR 0009: a regra continua em trigger, mas muda o que ela recusa.

**Contexto:** o ADR 0009 recusava qualquer exceção de permissão para o Admin. O diagrama do banco, que é a referência do modelo, diz "admin não aceita retirada" e "exceção só para conta interna". Uma exceção de acrescentar no Admin não muda nada, porque ele já tem todas as permissões.

**Decisão:** o trigger recusa exceção para conta que não é interna e, no Admin, recusa só exceção de retirada. Quem passa para o modelo Admin perde só as exceções de retirada. A conferência de permissão no backend continua liberando o Admin sem ler exceções.

**Por quê:** banco e diagrama passam a dizer a mesma coisa, e quem lê um não precisa conferir o outro. O efeito para o usuário é o mesmo: o Admin tem todas as permissões, sempre.
