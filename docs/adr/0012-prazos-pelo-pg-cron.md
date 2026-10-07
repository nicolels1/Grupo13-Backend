# Prazos da venda cancelados pelo banco, com pg_cron

**Contexto:** a reserva do checkout vence em 15 minutos e a retirada na loja em 7 dias depois de pronta (ADR 0002). Alguém precisa cancelar o que venceu. A API no plano gratuito do Render dorme depois de 15 minutos sem uso, então uma tarefa agendada dentro dela não roda; cancelar só quando alguém consulta deixaria peças presas na reserva e relatórios errados até a próxima consulta.

**Decisão:** a função `cancela_vencidos()` do banco (migration `03aeb347f6cf`) cancela os pedidos vencidos, e o pg_cron do Supabase a roda a cada minuto. Na reserva vencida, ela devolve as peças ao disponível e recusa a cobrança pendente; na retirada vencida, estorna o que foi pago, pelo mesmo método, e devolve as peças ao estoque online. Ela roda como dono do banco (`SECURITY DEFINER`), como o trigger do saldo. A API também confere o prazo ao receber uma cobrança, para não aprovar pagamento de reserva vencida que o pg_cron ainda não cancelou.

**Por quê:** o prazo vale mesmo com a API dormindo, e a regra fica no banco, como as outras garantias do estoque (ADR 0005). O custo é ter a mesma regra de cancelamento em SQL (pg_cron) e em Python (cancelamento pelo cliente, pela equipe e pagamento tardio).
