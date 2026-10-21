import uuid
from datetime import date

from sqlalchemy.orm import Session

from src.models.fidelidade import RegraFidelidade
from src.repositories import fidelidade_repository as repo
from src.use_cases.erros import RegraDeNegocio
from src.use_cases.estoque import BRASILIA
from src.use_cases.pedidos import agora

# programa de fidelidade de cada marca (P5, P10): as regras têm data e valem dali em diante; o saldo, a
# categoria e o FIFO dos pontos ficam no banco (trigger aplica_movimento_pontos, migration 3532b648a2ad)


def hoje() -> date:
    return agora().astimezone(BRASILIA).date()


# a vigente de cada marca é a de data mais recente até hoje; na mesma data, a criada por último
def listar_regras(db: Session, marca: str | None = None) -> list[dict]:
    regras = repo.listar_regras(db, marca)
    vigentes = {}
    for regra in regras:  # já vêm da mais nova para a mais antiga em cada marca
        if regra.valida_desde <= hoje():
            vigentes.setdefault(regra.marca, regra.id_regra)
    colunas = RegraFidelidade.__table__.columns
    return [{**{c.key: getattr(r, c.key) for c in colunas}, "vigente": vigentes.get(r.marca) == r.id_regra}
            for r in regras]


# a regra nova nunca muda um ponto já ganho: só vale de amanhã em diante (o banco também recusa)
def criar_regra(db: Session, id_usuario: uuid.UUID, dados: dict) -> dict:
    if dados["valida_desde"] <= hoje():
        raise RegraDeNegocio("A regra nova vale a partir de amanhã ou de uma data futura")
    regra = RegraFidelidade(**dados, id_criado_por=id_usuario)
    db.add(regra)
    db.commit()
    db.refresh(regra)
    return next(r for r in listar_regras(db, regra.marca) if r["id_regra"] == regra.id_regra)
