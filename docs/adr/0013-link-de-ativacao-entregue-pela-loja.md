# Link de ativação da conta do caixa entregue pela loja

**Substituída pela ADR 0014:** o caixa não cria mais conta, então não há link de ativação.

**Contexto:** a conta criada no caixa nasce sem senha e é ativada por link, confirmando o CPF (case, seção 5). Enquanto o servidor de e-mail próprio não estiver configurado, o Supabase só envia e-mail para quem é da equipe do projeto, então o cliente da loja não receberia o link.

**Decisão:** o backend gera o link pela API de admin do Supabase (`generate_link`), sem enviar e-mail, e devolve o link na resposta para a loja entregar ao cliente (QR code, mensagem). O link abre a página do frontend definida em `URL_ATIVACAO`; nela, o cliente já está identificado pelo token do Supabase e chama `POST /ativacao` com o CPF e a nova senha. Até ativar, a conta só consegue fazer isso. A loja pode gerar outro link a qualquer momento.

**Por quê:** a ativação funciona antes do servidor de e-mail e a regra do CPF continua no backend. Com o servidor de e-mail configurado, basta enviar o mesmo link por e-mail; o resto não muda.
