# Validação da migração da Vulto

Gerado por [`validar.py`](validar.py) em 21/10/2026 22:30, a partir do staging, do tratado e do banco. Para gerar de novo, depois do tratamento e da carga:

```bash
python -m migracao_vulto.validar --staging staging_vulto --tratado tratado_vulto --destino migracao_vulto/validacao.md
```

## Resultado

- **Da origem ao tratado:** 110.547 linhas da Vulto em 15 fontes; **0 sem explicação**. Cada linha que ficou de fora tem o motivo e a decisão abaixo.
- **Do tratado ao banco:** 16 tabelas; **todas com o mesmo número de registros no tratado e no banco**: a carga não rejeitou nada.
- **Pendências do tratamento:** 21 linhas que não se resolveram sem chute; ficam de fora, com o motivo (seção 3).

## 1. Da origem (Vulto) ao tratado

Uma linha é aproveitada quando virou registro na plataforma ou se juntou a um (a origem da migração, P4, guarda as duas).

| Fonte da Vulto | Linhas | Aproveitadas | Ficaram de fora | Viraram |
|---|---:|---:|---:|---|
| Lojas (`lojas`) | 7 | 7 | 0 | 5 unidades: o site e o app viram o canal online do CD Cajamar (V12) |
| Funcionários (`funcionarios`) | 24 | 13 | 11 | 13 contas internas inativas; com as 3 do SAC e a "Migração Vulto", 17 (P9) |
| Catálogo (5 abas da planilha) | 1.173 | 1.173 | 0 | produtos e variantes: o mesmo SKU em vários formatos vira uma variante (C13), a grade abre por tamanho (C20) |
| Vendas do sistema (`vendas`) | 18.598 | 18.202 | 396 | pedidos; a devolução (venda negativa) e a troca viram movimentação e estorno no pedido original (V6); a cópia do app se junta ao pedido (V5) |
| Itens das vendas (`itens_venda`) | 31.110 | 30.887 | 223 | itens dos pedidos e movimentações de estoque |
| Vendas manuais da Batel (planilha) | 76 | 72 | 4 | pedidos `VLT-BATEL-<linha>` (V9, V23) |
| Compras do Vulto+ (`compras`) | 14.948 | 0 | 14.948 | nenhuma tabela própria |
| Cadastro do Vulto+ (aba `Clientes`) | 2.535 | 2.535 | 0 | clientes sem conta: os cadastros do mesmo CPF viram um (K1) |
| Aba antiga do Vulto+ | 300 | 300 | 0 | clientes sem conta e o saldo de 2023 dos ATIVOS (K5c, K5d) |
| Movimentos de pontos (`pontos_mov`) | 12.127 | 12.108 | 19 | movimentos de pontos, mais os que a migração cria (saldo inicial K5a, dobro do online K4b, estornos K6) |
| Resgates anotados na planilha (aba `Resgates`) | 596 | 91 | 505 | resgates que só estão na planilha (K7a) |
| Atendimentos (WhatsApp, Instagram e e-mail) | 1.647 | 1.625 | 22 | chamados e as mensagens seguintes de cada conversa (P6) |
| Entradas de estoque (`entradas_estoque`) | 17.331 | 17.331 | 0 | recebimentos e transferências (E4) |
| Saldo do sistema (`estoque`) | 7.130 | 0 | 7.130 | nenhuma tabela: comparação no relatório da migração |
| Contagem de set/2026 e WMS do CD | 2.945 | 2.367 | 578 | saldo de cada peça em 01/10/2026 (E1, E2) |

### Por que ficaram de fora

**Funcionários (`funcionarios`)**

- 11: gerentes, operador do CD e contas genéricas (`gerente batel`, `admin`...): sem conta própria (P9)

**Vendas do sistema (`vendas`)**

- 328: troca lançada como venda nova: não vira pedido, a peça trocada entra como troca no pedido original (V6)
- 42: venda de teste em produção (V8)
- 26: relançamento da gerente da Batel: vale a venda da planilha (V9)

**Itens das vendas (`itens_venda`)**

- 134: item de cópia do mesmo pedido gravada pelo app (V5)
- 42: item de venda de teste em produção (V8)
- 38: item de relançamento da gerente da Batel: vale a venda da planilha (V9)
- 7: item de troca lançada como venda nova: não vira pedido, a peça trocada entra como troca no pedido original (V6)
- 2: devolução de item sem cadastro (DIVERSO): vira só o estorno no pedido original, sem peça para voltar ao estoque (P8, V6)

**Vendas manuais da Batel (planilha)**

- 4: linhas de subtotal da planilha ("TOTAL SEMANA", "TOTAL MARÇO"), não são vendas

**Compras do Vulto+ (`compras`)**

- 14.948: espelho das vendas no sistema do Vulto+: liga cada ponto à venda (K4b, K6), não vira tabela

**Movimentos de pontos (`pontos_mov`)**

- 14: pontos sem dono: V8 CPF de teste
- 5: pontos sem dono: K2 CPF de preenchimento

**Resgates anotados na planilha (aba `Resgates`)**

- 443: já registrado no banco como resgate (K7a)
- 60: antes de 01/01/2025: já descontado no saldo da planilha (K5a)
- 2: resgate só na planilha sem cliente identificado (K7a, margem de erro)

**Atendimentos (WhatsApp, Instagram e e-mail)**

- 22: spam e testes (A3)

**Saldo do sistema (`estoque`)**

- 7.130: saldo do sistema, que não migra: as lojas valem pela contagem e o CD pelo WMS (E1); o site e o app são cópia do CD (E3)

**Contagem de set/2026 e WMS do CD**

- 565: peça contada que já entra no saldo de 01/10 sem saldo inicial nem ajuste pela contagem: as entradas registradas explicam todas as peças dela (E2)
- 7: contagem por modelo, distribuída pelas variantes (E6)
- 6: a mesma peça contada em mais de uma linha (provador, vitrine...): os movimentos citam a primeira

## 2. Do tratado ao banco

A carga roda numa transação só e só grava se tudo passar nas regras do banco e os três números baterem.

| Tabela | Tratado | Banco | Diferença |
|---|---:|---:|---|
| `unidade` | 5 | 5 | igual |
| `usuario` | 17 | 17 | igual |
| `produto` | 181 | 181 | igual |
| `variante` | 893 | 893 | igual |
| `historico_preco` | 1.178 | 1.178 | igual |
| `cliente_sem_conta` | 4.318 | 4.318 | igual |
| `pedido` | 17.997 | 17.997 | igual |
| `item_pedido` | 30.485 | 30.485 | igual |
| `pagamento` | 17.504 | 17.504 | igual |
| `chamado` | 1.625 | 1.625 | igual |
| `mensagem` | 1.642 | 1.642 | igual |
| `transferencia` | 13.147 | 13.147 | igual |
| `movimentacao_estoque` | 62.119 | 62.119 | igual |
| `estoque` | 3.494 | 3.494 | igual (só as posições com peça: o banco cria a linha zerada de toda peça e unidade por onde o histórico passa) |
| `movimento_pontos` | 17.173 | 17.173 | igual |
| `origem_migracao` | 183.872 | 183.872 | igual |

## 3. Pendências do tratamento

O que não se resolveu sem chute (um valor sem tradução, uma ligação que não fecha). Não entra no banco.

| Tabela | Motivo | Linhas |
|---|---|---:|
| `movimento_pontos` | pontos sem dono: V8 CPF de teste | 14 |
| `movimento_pontos` | pontos sem dono: K2 CPF de preenchimento | 5 |
| `movimento_pontos` | resgate só na planilha sem cliente identificado (K7a, margem de erro) | 2 |
