# Assistente do cliente no n8n (Entrega 4)

Os fluxos do n8n que ligam o bot do Telegram às rotas `/assistente` deste backend. O n8n recebe a mensagem, decide o caminho (fluxo fixo ou IA) e chama o backend. A regra de negócio, os dados e a segurança ficam todos no backend.

Os arquivos são exportações do n8n (menu do workflow → Download). **Não têm senhas nem chaves:** cada nó guarda só o nome da credencial, e os valores ficam na conta do n8n.

## Os fluxos

| Arquivo | Workflow no n8n | O que faz |
|---|---|---|
| [cliente-principal.json](cliente-principal.json) | Cliente - Principal | Entrada pelo Telegram, situação da conversa, IA com ferramentas e saída. É o único com gatilho; os outros são sub-workflows chamados por ele |
| [backend-auxiliar.json](backend-auxiliar.json) | Backend - Auxiliar | O único lugar com chamada HTTP ao backend. Recebe uma `operacao` (`buscar_pecas`, `meus_pontos`, `abrir_chamado`…) e monta a rota, com a chave no cabeçalho, timeout de 90 s e uma retentativa. Responde sempre `{ ok, status, dados, detail }` |
| [responder-auxiliar.json](responder-auxiliar.json) | Responder - Auxiliar | Divide a resposta em mensagens, troca os marcadores da IA (`[[peca:ID]]`, `[[loja:ID]]`, `[[carrinho]]`, `[[menu]]`…) por cartões e botões e envia pelo canal |
| [atalhos-auxiliar.json](atalhos-auxiliar.json) | Atalhos - Auxiliar | Respostas fixas, sem IA: `/start`, mensagem sem texto (foto ou PDF vira anexo do chamado), 👍/👎 e os botões do lembrete de pontos |
| [confirmacao-auxiliar.json](confirmacao-auxiliar.json) | Confirmação - Auxiliar | Confirmação da conta por CPF e código enviado por e-mail, sem IA |

## O caminho de uma mensagem

```
Telegram Trigger
  → Normalizador                 { canal, id_externo, tipo, texto, ... }
  → Registra a mensagem          POST /assistente/conversa: etapa, limite do dia, conversa nova
  → Atalhos sem IA               /start, anexo, 👍/👎, lembrete            (Atalhos - Auxiliar)
  → Equipe conversando?          a equipe respondeu há menos de 30 min: vai só para o chamado
  → Pela etapa
      esperando_cpf / _codigo  → Confirmação por CPF                       (Confirmação - Auxiliar)
      confirmando_chamado      → Enviar ou Corrigir o chamado
      livre                    → AI Agent (Claude + memória + ferramentas)
  → Resposta final → Responder                                              (Responder - Auxiliar)
  → Guarda o texto               POST /assistente/conversa/mensagens (90 dias, CPF e código mascarados)
```

Duas regras valem para o fluxo todo:

- **`canal` e `id_externo` vêm sempre do Normalizador, nunca da IA.** A IA escolhe a ferramenta e os parâmetros (peça, cor, tamanho), mas não de quem é o dado. Por isso ela não consegue ver os pontos ou os pedidos de outra pessoa.
- **CPF e código não passam pela IA nem pela memória.** A confirmação roda em um sub-workflow fixo.

Cada ferramenta da IA (`buscar_pecas`, `ver_estoque`, `link_carrinho`, `lojas`, `politicas`, `meus_pontos`, `extrato_pontos`, `meus_pedidos`, `rascunhar_chamado`, `lembrete_pontos`) chama o Backend - Auxiliar com uma operação fixa. As rotas e o que cada uma responde estão no [Swagger](https://grupo13-backend-megw.onrender.com/docs), na seção **assistente**.

## Como importar

1. Crie as credenciais no n8n, com estes nomes:

   | Credencial | Tipo | Valor |
   |---|---|---|
   | Chave do backend | Header Auth | Name `X-Assistente-Chave`; Value igual à variável `ASSISTENTE_CHAVE` do Render |
   | Telegram account | Telegram API | Token do bot, gerado no @BotFather |
   | Anthropic account | Anthropic | Chave da API da Anthropic (modelo Claude Sonnet 5.5) |
   | Supabase (memória) | Postgres | Pooler do Supabase com o usuário `n8n_casalorenzi.<ref do projeto>`, criado por uma migration só para isso. Ele enxerga apenas a tabela `memoria_assistente.n8n_chat_histories`, e nenhuma tabela do negócio |

2. Crie a Data Table `anexos_em_album`, com a coluna `grupo` (texto). O Atalhos - Auxiliar usa essa tabela para mandar um aviso só quando o cliente envia um álbum de fotos.
3. Importe os sub-workflows primeiro (Backend, Responder, Atalhos e Confirmação) e o Cliente - Principal por último (menu → Import from File).
4. Em outra conta do n8n, os ids mudam. Abra os nós **Execute Workflow** e as ferramentas da IA e escolha de novo o sub-workflow certo. Faça o mesmo nos nós da Data Table e nas credenciais.
5. Ative o Cliente - Principal. O backend no Render dorme depois de 15 minutos parado, então a primeira mensagem pode levar cerca de 1 minuto.
