# permissões da Gestão: só o Admin tem; nem modelo comum nem exceção concedem
PERMISSOES_SO_ADMIN = {"gerenciar_contas", "gerenciar_modelos_acesso", "gerenciar_unidades"}


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
