# Carrega horários de exemplo nas lojas ativas, para o assistente do cliente responder "que horas fecha?".
# Os horários não vieram do cliente: são exemplo, e cada loja cadastra o seu. Loja de shopping (pelo nome
# ou endereço) segue o horário do shopping; as outras, o de rua. Feriados nacionais dos próximos 12 meses
# entram como exceção: rua fechada, shopping das 14h às 20h; Natal e Ano-Novo todas fechadas; véspera de
# Natal o shopping fecha às 18h.
# Pode rodar de novo: loja que já tem horário não é mexida e exceção que já existe é pulada.
# Uso, na raiz do repositório com o venv ativo:
#   python -m scripts.carregar_horarios
# Precisa de DATABASE_URL no .env.
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.estoque import Unidade
from src.models.horarios import ExcecaoHorario, HorarioUnidade
from src.use_cases.horarios import BRASILIA, FERIADOS_NACIONAIS

RUA = {dia: (time(10), time(20)) for dia in range(5)} | {5: (time(10), time(18))}
SHOPPING = {dia: (time(10), time(22)) for dia in range(6)} | {6: (time(14), time(20))}
FERIADO_NO_SHOPPING = (time(14), time(20))
TODAS_FECHADAS = {(12, 25), (1, 1)}
VESPERA_DE_NATAL = (12, 24)


def eh_shopping(unidade: Unidade) -> bool:
    endereco = " ".join(filter(None, (unidade.nome, unidade.rua, unidade.complemento, unidade.bairro)))
    return "shopping" in endereco.lower()


def horarios_da_semana(shopping: bool) -> dict[int, tuple[time, time]]:
    return SHOPPING if shopping else RUA


# (data, abre, fecha, motivo) dos próximos 12 meses a partir de hoje; abre e fecha vazios = fechada
def excecoes(shopping: bool, hoje: date) -> list[tuple[date, time | None, time | None, str]]:
    lista = []
    for deslocamento in range(366):
        dia = hoje + timedelta(days=deslocamento)
        chave = (dia.month, dia.day)
        if chave in FERIADOS_NACIONAIS:
            motivo = FERIADOS_NACIONAIS[chave]
            aberta = shopping and chave not in TODAS_FECHADAS
            abre, fecha = FERIADO_NO_SHOPPING if aberta else (None, None)
            lista.append((dia, abre, fecha, motivo))
        elif chave == VESPERA_DE_NATAL and shopping:
            lista.append((dia, time(10), time(18), "véspera de Natal"))
    return lista


def carregar(db: Session, hoje: date) -> dict:
    lojas = db.scalars(select(Unidade).where(Unidade.tipo == "loja", Unidade.ativo)).all()
    com_horario = set(db.scalars(select(HorarioUnidade.id_unidade).distinct()))
    ja_cadastradas = {(i, dia) for i, dia in db.execute(select(ExcecaoHorario.id_unidade, ExcecaoHorario.data))}
    contagem = {"lojas": 0, "excecoes": 0}
    for loja in lojas:
        shopping = eh_shopping(loja)
        if loja.id_unidade not in com_horario:
            for dia, (abre, fecha) in horarios_da_semana(shopping).items():
                db.add(HorarioUnidade(id_unidade=loja.id_unidade, dia_semana=dia, abre=abre, fecha=fecha))
            contagem["lojas"] += 1
        for dia, abre, fecha, motivo in excecoes(shopping, hoje):
            if (loja.id_unidade, dia) not in ja_cadastradas:
                db.add(ExcecaoHorario(id_unidade=loja.id_unidade, data=dia, abre=abre, fecha=fecha, motivo=motivo))
                contagem["excecoes"] += 1
    db.commit()
    return contagem


def main() -> None:
    # importado aqui para as funções acima poderem ser testadas sem conectar no banco
    from src.database.session import SessionLocal

    with SessionLocal() as db:
        contagem = carregar(db, datetime.now(BRASILIA).date())
    print(f"Lojas com horário novo: {contagem['lojas']}, exceções novas: {contagem['excecoes']}")


if __name__ == "__main__":
    main()
