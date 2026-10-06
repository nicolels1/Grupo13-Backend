from pydantic import BaseModel

LIMITE_PADRAO = 50
LIMITE_MAXIMO = 200


# listagem grande sempre paginada: devolve uma página e o total, para o frontend montar a navegação
class Pagina[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int
