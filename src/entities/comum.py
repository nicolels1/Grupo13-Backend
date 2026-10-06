from pydantic import BaseModel

LIMITE_PADRAO = 50
LIMITE_MAXIMO = 200


# listagem grande sempre paginada: devolve uma página e o total, para o frontend montar a navegação
class Pagina[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


# lista curta, sem paginação (ex.: categorias, unidades): {"items": [...]}
class Lista[T](BaseModel):
    items: list[T]


# tira espaços das pontas de todo texto que chega; usado como validator "before"
def sem_espacos(valor):
    return valor.strip() if isinstance(valor, str) else valor


# campos enviados num PATCH; null só vale para os campos que aceitam vazio
def campos_alterados(dados: BaseModel, nullaveis: tuple[str, ...] = ()) -> dict:
    return {
        campo: valor
        for campo, valor in dados.model_dump(exclude_unset=True).items()
        if valor is not None or campo in nullaveis
    }
