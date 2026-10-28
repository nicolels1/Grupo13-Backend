from datetime import date, time

from sqlalchemy import CheckConstraint, Date, ForeignKey, SmallInteger, String, Time
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base


# horário de funcionamento de uma loja em cada dia da semana (0 = segunda ... 6 = domingo, como o
# weekday() do Python). Dia sem linha é dia fechado; um intervalo só por dia
class HorarioUnidade(Base):
    __tablename__ = "horario_unidade"

    id_unidade: Mapped[int] = mapped_column(
        ForeignKey("unidade.id_unidade", ondelete="RESTRICT"), primary_key=True
    )
    dia_semana: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    abre: Mapped[time] = mapped_column(Time)
    fecha: Mapped[time] = mapped_column(Time)

    __table_args__ = (
        CheckConstraint("dia_semana BETWEEN 0 AND 6", name="dia_semana_valido"),
        CheckConstraint("abre < fecha", name="abre_antes_de_fechar"),
    )


# feriado ou horário especial de uma data (ex.: Natal, horário do shopping); vale mais que o horário
# da semana. Sem abre e fecha, a loja fica fechada nessa data
class ExcecaoHorario(Base):
    __tablename__ = "excecao_horario"

    id_unidade: Mapped[int] = mapped_column(
        ForeignKey("unidade.id_unidade", ondelete="RESTRICT"), primary_key=True
    )
    data: Mapped[date] = mapped_column(Date, primary_key=True)
    abre: Mapped[time | None] = mapped_column(Time)
    fecha: Mapped[time | None] = mapped_column(Time)
    motivo: Mapped[str] = mapped_column(String(100))

    __table_args__ = (
        CheckConstraint("(abre IS NULL) = (fecha IS NULL)", name="abre_e_fecha_juntos"),
        CheckConstraint("abre IS NULL OR abre < fecha", name="excecao_abre_antes_de_fechar"),
    )
