from sqlalchemy.dialects import postgresql

from src.repositories import fidelidade_repository


def sql(consulta):
    return str(consulta.compile(dialect=postgresql.dialect()))


# o passivo soma o que sobra em cada lote pelo valor do ponto da época (K3), separado por marca (P10)
def test_passivo_por_marca_pelo_valor_da_epoca():
    consulta = sql(fidelidade_repository.consulta_passivo())

    assert "sum(lote_pontos.restante * lote_pontos.valor_ponto)" in consulta
    assert "WHERE lote_pontos.restante > " in consulta
    assert "GROUP BY lote_pontos.marca" in consulta
