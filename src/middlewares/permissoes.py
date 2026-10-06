import logging
import uuid

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from src.database.session import get_db
from src.middlewares.auth import get_current_user
from src.models.contas import Usuario
from src.repositories import permissao_repository as repo
from src.use_cases.permissoes import tem_permissao

logger = logging.getLogger(__name__)


# busca o USUARIO pelo id do token e exige conta ativa; lê do banco a cada ação (ADR 0001)
def get_usuario_ativo(
    user_id: str = Depends(get_current_user), db: Session = Depends(get_db)
) -> Usuario:
    try:
        id_usuario = uuid.UUID(user_id)
    except ValueError:
        logger.warning("token com id de usuário malformado: %s", user_id)
        raise HTTPException(status_code=401, detail="Token inválido")

    usuario = repo.buscar_usuario(db, id_usuario)
    if usuario is None:
        raise HTTPException(status_code=403, detail="Usuário não cadastrado")
    if usuario.status_conta != "ativa":
        raise HTTPException(status_code=403, detail="Conta não está ativa")
    return usuario


# uso: usuario: Usuario = Depends(exige_permissao("movimentar_estoque"))
def exige_permissao(codigo: str):
    def dependencia(
        usuario: Usuario = Depends(get_usuario_ativo), db: Session = Depends(get_db)
    ) -> Usuario:
        # só conta interna tem permissões; cliente não tem modelo de acesso
        if usuario.tipo_conta != "interna" or usuario.id_modelo_acesso is None:
            raise HTTPException(status_code=403, detail="Sem permissão")

        # admin não precisa das outras consultas
        if repo.modelo_eh_admin(db, usuario.id_modelo_acesso):
            return usuario

        excecoes = repo.excecoes_do_usuario(db, usuario.id_usuario)
        permitido = tem_permissao(
            codigo,
            eh_admin=False,
            do_modelo=repo.codigos_do_modelo(db, usuario.id_modelo_acesso),
            acrescentadas={c for c, efeito in excecoes.items() if efeito == "acrescentar"},
            retiradas={c for c, efeito in excecoes.items() if efeito == "retirar"},
        )
        if not permitido:
            raise HTTPException(status_code=403, detail="Sem permissão")
        return usuario

    return dependencia
