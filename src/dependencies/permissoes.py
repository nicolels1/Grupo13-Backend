import logging
import uuid

from fastapi import Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from src.database.session import get_db
from src.dependencies.auth import get_current_user
from src.entities.comum import Marca
from src.models.contas import Usuario
from src.repositories import marca_repository
from src.repositories import permissao_repository as repo
from src.use_cases.permissoes import (
    exigir_marca, marca_da_consulta, marcas_visiveis, usuario_tem_permissao,
)

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


# rota pública que mostra mais para quem está logado: sem o header, ninguém (None);
# com header, o token precisa ser válido e a conta ativa, como nas rotas protegidas
def get_usuario_opcional(
    authorization: str | None = Header(None), db: Session = Depends(get_db)
) -> Usuario | None:
    if authorization is None:
        return None
    return get_usuario_ativo(get_current_user(authorization), db)


# uso: usuario: Usuario = Depends(exige_permissao("movimentar_estoque"))
# com mais de um código, basta ter um deles (ex.: ver o estoque)
def exige_permissao(*codigos: str):
    def dependencia(
        usuario: Usuario = Depends(get_usuario_ativo), db: Session = Depends(get_db)
    ) -> Usuario:
        if not usuario_tem_permissao(db, usuario, *codigos):
            raise HTTPException(status_code=403, detail="Sem permissão")
        return usuario

    return dependencia


# filtro ?marca= das listas da equipe: só a marca que a pessoa vê (ver_marca_lorenzi, ver_marca_vulto);
# sem pedir, quem vê uma marca só recebe essa, e quem vê as duas recebe tudo (None)
def marca_permitida(
    marca: Marca | None = Query(None, description="lorenzi ou vulto. Vazio: as marcas que a pessoa vê"),
    usuario: Usuario = Depends(get_usuario_ativo),
    db: Session = Depends(get_db),
) -> str | None:
    return marca_da_consulta(marcas_visiveis(db, usuario), marca)


def marcas_vistas(usuario: Usuario = Depends(get_usuario_ativo), db: Session = Depends(get_db)) -> set[str]:
    return marcas_visiveis(db, usuario)


# detalhe e escrita de um registro pelo id do caminho: só de uma marca que a pessoa vê. A moderação de
# avaliações fica de fora: quem modera cuida da rede inteira
# Uso: @router.post(..., dependencies=[Depends(marca_do_pedido)])
def marca_do_pedido(id_pedido: int, usuario: Usuario = Depends(get_usuario_ativo), db: Session = Depends(get_db)):
    exigir_marca(db, usuario, marca_repository.do_pedido(db, id_pedido))


def marca_do_chamado(id_chamado: int, usuario: Usuario = Depends(get_usuario_ativo), db: Session = Depends(get_db)):
    exigir_marca(db, usuario, marca_repository.do_chamado(db, id_chamado))


def marca_da_transferencia(
    id_transferencia: int, usuario: Usuario = Depends(get_usuario_ativo), db: Session = Depends(get_db),
):
    exigir_marca(db, usuario, marca_repository.da_transferencia(db, id_transferencia))


def marca_do_produto(id_produto: int, usuario: Usuario = Depends(get_usuario_ativo), db: Session = Depends(get_db)):
    exigir_marca(db, usuario, marca_repository.do_produto(db, id_produto))


def marca_da_variante(id_variante: int, usuario: Usuario = Depends(get_usuario_ativo), db: Session = Depends(get_db)):
    exigir_marca(db, usuario, marca_repository.da_variante(db, id_variante))


def marca_da_imagem(id_imagem: int, usuario: Usuario = Depends(get_usuario_ativo), db: Session = Depends(get_db)):
    exigir_marca(db, usuario, marca_repository.da_imagem(db, id_imagem))


# rotas da plataforma do cliente: conta interna usa a conta pessoal de cliente para comprar
def exige_cliente(usuario: Usuario = Depends(get_usuario_ativo)) -> Usuario:
    if usuario.tipo_conta != "cliente":
        raise HTTPException(status_code=403, detail="Só para contas de cliente")
    return usuario
