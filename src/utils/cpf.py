# CPF guardado só com os 11 dígitos (USUARIO.cpf é String(11)); aceita com ou sem máscara
def normalizar_cpf(cpf: str) -> str:
    return "".join(c for c in cpf if c.isdigit())


def _digito(base: str) -> str:
    peso_inicial = len(base) + 1
    soma = sum(int(d) * peso for d, peso in zip(base, range(peso_inicial, 1, -1)))
    resto = soma * 10 % 11
    return str(resto % 10)


# confere tamanho e os dois dígitos verificadores; recusa sequências repetidas (111.111.111-11)
def cpf_valido(cpf: str) -> bool:
    if len(cpf) != 11 or not cpf.isdigit() or cpf == cpf[0] * 11:
        return False
    return cpf[9] == _digito(cpf[:9]) and cpf[10] == _digito(cpf[:10])
