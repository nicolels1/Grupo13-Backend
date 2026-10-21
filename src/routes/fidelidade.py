from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from src.database.session import get_db
from src.dependencies.permissoes import exige_cliente, exige_permissao
from src.entities.comum import Lista, Marca
from src.entities.fidelidade import RegraCriar, RegraSaida, ResgateSimulado
from src.models.contas import Usuario
from src.use_cases import fidelidade
from src.use_cases.erros import RegraDeNegocio
from src.utils.cpf import cpf_valido, normalizar_cpf

router = APIRouter(prefix="/fidelidade", tags=["fidelidade"])

GERENCIAR = exige_permissao("gerenciar_fidelidade")


@router.get(
    "/regras", response_model=Lista[RegraSaida],
    description="Regras de cada marca, da mais nova para a mais antiga; `vigente` marca a que vale hoje.",
)
def listar_regras(
    marca: Marca | None = Query(None), db: Session = Depends(get_db), usuario: Usuario = Depends(GERENCIAR),
):
    return {"items": fidelidade.listar_regras(db, marca)}


@router.post(
    "/regras", status_code=status.HTTP_201_CREATED, response_model=RegraSaida,
    description="Cria a regra de uma marca a partir de uma data futura. Vale dali em diante e nunca muda um "
                "ponto já ganho; uma regra que ainda não começou se corrige com outra para a mesma data.",
)
def criar_regra(dados: RegraCriar, db: Session = Depends(get_db), usuario: Usuario = Depends(GERENCIAR)):
    return fidelidade.criar_regra(db, usuario.id_usuario, dados.model_dump())


# ---------- resgate ----------

@router.get(
    "/resgate", response_model=ResgateSimulado,
    description="Quanto valem os pontos do cliente logado num resgate na marca, antes do checkout (`pontos_resgate`).",
)
def simular_resgate(
    marca: Marca, pontos: int = Query(gt=0), cliente: Usuario = Depends(exige_cliente), db: Session = Depends(get_db),
):
    return fidelidade.simular_resgate(db, cliente.id_usuario, None, marca, pontos)


@router.get(
    "/resgate/caixa", response_model=ResgateSimulado,
    description="No caixa: quanto valem os pontos do cliente do CPF (com conta ou da Vulto sem conta) antes da venda.",
)
def simular_resgate_no_caixa(
    cpf: str, marca: Marca, pontos: int = Query(gt=0), db: Session = Depends(get_db),
    usuario: Usuario = Depends(exige_permissao("registrar_venda_fisica")),
):
    cpf = normalizar_cpf(cpf)
    if not cpf_valido(cpf):
        raise RegraDeNegocio("CPF inválido")
    id_cliente, id_cliente_sem_conta = fidelidade.dono_pelo_cpf(db, cpf)
    return fidelidade.simular_resgate(db, id_cliente, id_cliente_sem_conta, marca, pontos)
