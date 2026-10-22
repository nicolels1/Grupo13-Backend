from sqlalchemy.orm import Session

from src.repositories import marca_repository
from src.repositories import permissao_repository as repo
from src.use_cases.erros import SemPermissao

# permissões da Gestão: só o Admin tem; nem modelo comum nem exceção concedem
PERMISSOES_SO_ADMIN = {"gerenciar_contas", "gerenciar_modelos_acesso", "gerenciar_unidades"}
# que marca a pessoa vê nas listas da equipe: o Admin dá uma, a outra ou as duas
PERMISSOES_DE_MARCA = {"ver_marca_lorenzi": "lorenzi", "ver_marca_vulto": "vulto"}


def tem_permissao(
    codigo: str,
    *,
    eh_admin: bool,
    do_modelo: set[str],
    acrescentadas: set[str],
    retiradas: set[str],
) -> bool:
    # Admin tem todas, inclusive futuras, e ignora exceções (ADRs 0009 e 0010)
    if eh_admin:
        return True
    if codigo in PERMISSOES_SO_ADMIN:
        return False
    # permissão efetiva: as do modelo, mais as exceções acrescentar, menos as retirar
    return codigo in (do_modelo | acrescentadas) - retiradas


# lista mostrada ao frontend (GET /me) para esconder o que a pessoa não pode usar;
# segue a mesma regra de tem_permissao
def permissoes_efetivas(
    *,
    eh_admin: bool,
    todas: set[str],
    do_modelo: set[str],
    acrescentadas: set[str],
    retiradas: set[str],
) -> set[str]:
    if eh_admin:
        return set(todas)
    return (do_modelo | acrescentadas) - retiradas - PERMISSOES_SO_ADMIN


# a pessoa tem pelo menos uma das permissões? lê do banco a cada chamada (ADR 0001)
def usuario_tem_permissao(db: Session, usuario, *codigos: str) -> bool:
    # só conta interna tem permissões; cliente não tem modelo de acesso
    if usuario.tipo_conta != "interna" or usuario.id_modelo_acesso is None:
        return False
    # admin não precisa das outras consultas
    if repo.modelo_eh_admin(db, usuario.id_modelo_acesso):
        return True

    excecoes = repo.excecoes_do_usuario(db, usuario.id_usuario)
    do_modelo = repo.codigos_do_modelo(db, usuario.id_modelo_acesso)
    return any(
        tem_permissao(
            codigo,
            eh_admin=False,
            do_modelo=do_modelo,
            acrescentadas={c for c, efeito in excecoes.items() if efeito == "acrescentar"},
            retiradas={c for c, efeito in excecoes.items() if efeito == "retirar"},
        )
        for codigo in codigos
    )


def marcas_visiveis(db: Session, usuario) -> set[str]:
    return {marca for codigo, marca in PERMISSOES_DE_MARCA.items() if usuario_tem_permissao(db, usuario, codigo)}


# a marca pedida, se a pessoa a vê; sem pedir, a única que ela vê, ou None quando vê as duas
def marca_da_consulta(visiveis: set[str], pedida: str | None) -> str | None:
    if pedida is not None:
        if pedida not in visiveis:
            raise SemPermissao(f"Sem acesso à marca {pedida}")
        return pedida
    if not visiveis:
        raise SemPermissao("Sem acesso a nenhuma marca")
    return next(iter(visiveis)) if len(visiveis) == 1 else None


# a pessoa só abre ou muda o que é de uma marca que ela vê; o que não tem marca (a dúvida geral) passa
def conferir_marca(visiveis: set[str], marcas: set[str]) -> None:
    if marcas and not marcas & visiveis:
        raise SemPermissao(f"Sem acesso à marca {' e '.join(sorted(marcas))}")


def exigir_marca(db: Session, usuario, marcas: set[str]) -> None:
    conferir_marca(marcas_visiveis(db, usuario), marcas)


# escrita que informa a unidade no corpo (movimentação, mínimo, realocação, transferência, venda na loja)
def exigir_marca_da_unidade(db: Session, usuario, id_unidade: int) -> None:
    exigir_marca(db, usuario, marca_repository.da_unidade(db, id_unidade))
