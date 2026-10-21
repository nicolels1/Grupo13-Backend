import uuid

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from src.models.atendimento import Chamado
from src.models.contas import ClienteSemConta
from src.models.vendas import Pedido


def buscar(db: Session, id_cliente_sem_conta: int) -> ClienteSemConta | None:
    return db.get(ClienteSemConta, id_cliente_sem_conta)


# cliente da Vulto com esse CPF que ainda não criou conta (o caixa liga a venda a ele, P2)
def buscar_por_cpf(db: Session, cpf: str) -> ClienteSemConta | None:
    return db.scalar(select(ClienteSemConta).where(ClienteSemConta.cpf == cpf, ClienteSemConta.id_usuario.is_(None)))


# consulta da equipe: só dígitos procura CPF ou telefone exatos; texto procura parte do nome ou do e-mail
def listar(db: Session, limit: int, offset: int, busca: str | None = None) -> tuple[list[ClienteSemConta], int]:
    consulta = select(ClienteSemConta)
    termo = (busca or "").strip()
    digitos = "".join(c for c in termo if c.isdigit())
    if termo and digitos and not any(c.isalpha() or c == "@" for c in termo):
        consulta = consulta.where(or_(ClienteSemConta.cpf == digitos, ClienteSemConta.telefone == digitos))
    elif termo:
        padrao = f"%{termo}%"
        consulta = consulta.where(or_(ClienteSemConta.nome.ilike(padrao), ClienteSemConta.email.ilike(padrao)))
    total = db.scalar(select(func.count()).select_from(consulta.subquery()))
    pagina = consulta.order_by(ClienteSemConta.nome, ClienteSemConta.id_cliente_sem_conta).limit(limit).offset(offset)
    return list(db.scalars(pagina)), total


# o cliente sem conta da pessoa que acabou de ter conta: o do CPF dela e o do e-mail dela entre quem
# não tem CPF (e-mail de família não junta dois CPFs, P2). Só os que ainda não passaram para outra conta
def _da_pessoa(cpf: str | None, email: str | None):
    condicoes = []
    if cpf:
        condicoes.append(ClienteSemConta.cpf == cpf)
    if email:
        condicoes.append(and_(ClienteSemConta.cpf.is_(None), func.lower(ClienteSemConta.email) == email.lower()))
    return select(ClienteSemConta.id_cliente_sem_conta).where(
        ClienteSemConta.id_usuario.is_(None), or_(*condicoes)
    )


# pedidos e chamados do cliente sem conta passam para a conta, e ele guarda para qual foram (ADR 0016);
# devolve quantos pedidos foram ligados
def passar_para_conta(db: Session, id_usuario: uuid.UUID, cpf: str | None, email: str | None) -> int:
    if not cpf and not email:
        return 0
    ids = list(db.scalars(_da_pessoa(cpf, email)))
    if not ids:
        return 0
    pedidos = db.execute(
        update(Pedido).where(Pedido.id_cliente_sem_conta.in_(ids))
        .values(id_cliente=id_usuario, id_cliente_sem_conta=None)
    )
    db.execute(
        update(Chamado).where(Chamado.id_cliente_sem_conta.in_(ids))
        .values(id_cliente=id_usuario, id_cliente_sem_conta=None)
    )
    db.execute(
        update(ClienteSemConta).where(ClienteSemConta.id_cliente_sem_conta.in_(ids)).values(id_usuario=id_usuario)
    )
    return pedidos.rowcount
