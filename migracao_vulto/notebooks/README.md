# Notebooks da migração da Vulto

Análise exploratória: entender cada fonte, ver exemplos e testar regras antes de elas irem para os scripts. O que vale para a migração é o que está nos scripts (`padronizar.py`, `regras.py` e `tratar.py`); o notebook mostra o raciocínio e a evidência de cada decisão do [registro de decisões](../registro_decisoes.md).

## Regras

- **Leem só os CSVs gerados** (`staging_vulto/`, `tratado_vulto/`), nunca os arquivos originais da Vulto e nunca gravam neles.
- **Saídas limpas antes de cada commit** (*Kernel → Restart & Clear Output*): as tabelas mostradas têm CPF, e-mail e telefone de clientes.
- **Regra testada aqui e aprovada vai para o script.** O notebook não corrige dado que entra no banco.
- Um notebook por fonte ou assunto, numerado na ordem de leitura: `01_vendas.ipynb`, `02_catalogo.ipynb`...

## Como abrir

Na raiz do repositório, depois de gerar o `staging_vulto/`:

```bash
python -m jupyter notebook migracao_vulto/notebooks
```
