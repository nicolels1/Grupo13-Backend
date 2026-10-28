from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.models.horarios import ExcecaoHorario, HorarioUnidade


def semana(db: Session, id_unidade: int) -> list[HorarioUnidade]:
    consulta = select(HorarioUnidade).where(HorarioUnidade.id_unidade == id_unidade).order_by(HorarioUnidade.dia_semana)
    return list(db.scalars(consulta))


# a semana nova substitui a antiga inteira; quem chama faz o commit, e as duas coisas valem juntas
def trocar_semana(db: Session, id_unidade: int, dias: list[HorarioUnidade]) -> None:
    db.execute(delete(HorarioUnidade).where(HorarioUnidade.id_unidade == id_unidade))
    db.add_all(dias)


def excecoes_a_partir(db: Session, id_unidade: int, dia: date) -> list[ExcecaoHorario]:
    consulta = (
        select(ExcecaoHorario)
        .where(ExcecaoHorario.id_unidade == id_unidade, ExcecaoHorario.data >= dia)
        .order_by(ExcecaoHorario.data)
    )
    return list(db.scalars(consulta))


def buscar_excecao(db: Session, id_unidade: int, dia: date) -> ExcecaoHorario | None:
    return db.get(ExcecaoHorario, (id_unidade, dia))
