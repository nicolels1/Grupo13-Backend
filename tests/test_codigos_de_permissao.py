import importlib.util
from pathlib import Path
from typing import get_args

from src.entities.modelos_acesso import CodigoPermissao

PASTA_MIGRACOES = Path(__file__).resolve().parent.parent / "migrations" / "versions"


# códigos que as migrações cadastram: a lista PERMISSOES (várias) ou PERMISSAO (uma só) de cada arquivo
def codigos_das_migracoes() -> set[str]:
    codigos: set[str] = set()
    for arquivo in sorted(PASTA_MIGRACOES.glob("*.py")):
        spec = importlib.util.spec_from_file_location(arquivo.stem, arquivo)
        migracao = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migracao)
        codigos.update(codigo for codigo, _ in getattr(migracao, "PERMISSOES", []))
        if hasattr(migracao, "PERMISSAO"):
            codigos.add(migracao.PERMISSAO[0])
    return codigos


# o OpenAPI publica CodigoPermissao para o frontend: se faltar um código cadastrado, a resposta
# com ele falharia na validação; se sobrar um, o frontend aceitaria um código que não existe
def test_codigos_de_permissao_iguais_aos_das_migracoes():
    assert set(get_args(CodigoPermissao)) == codigos_das_migracoes()
