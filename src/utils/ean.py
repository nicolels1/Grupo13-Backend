# EAN-13: código de barras da etiqueta da peça (C12). A mesma conta está no banco, na função
# ean_valido (migration que cria variante.ean)
# Prefixo 20: faixa que a GS1 reserva para uso interno da loja; é o das peças sem código de fábrica
PREFIXO_INTERNO = "20"


# dígito verificador: soma os 12 primeiros com pesos 1 e 3 alternados e completa até a dezena
def digito_verificador(base: str) -> str:
    soma = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(base))
    return str((10 - soma % 10) % 10)


# 13 dígitos com o verificador certo; recusa o EAN que o Excel cortou (7890231000000 tem o verificador errado)
def ean_valido(ean: str) -> bool:
    return len(ean) == 13 and ean.isdigit() and ean[12] == digito_verificador(ean[:12])


# código interno da variante sem EAN de fábrica: 20 + número da variante + verificador
def ean_interno(id_variante: int) -> str:
    base = f"{PREFIXO_INTERNO}{id_variante:010d}"
    return base + digito_verificador(base)
