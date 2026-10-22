import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from src.database.session import get_db
from src.dependencies.permissoes import exige_cliente, exige_permissao, marca_permitida, marcas_vistas
from src.entities.comum import LIMITE_MAXIMO, LIMITE_PADRAO, Lista, Marca, Pagina
from src.entities.fidelidade import (
    MovimentoPontosSaida, PassivoSaida, PontosDoCliente, RegraCriar, RegraSaida, ResgateSimulado,
)
from src.models.contas import Usuario
from src.use_cases import fidelidade
from src.use_cases.erros import RegraDeNegocio
from src.use_cases.permissoes import conferir_marca
from src.utils.cpf import cpf_valido, normalizar_cpf

router = APIRouter(prefix="/fidelidade", tags=["fidelidade"])

GERENCIAR = exige_permissao("gerenciar_fidelidade")


@router.get(
    "/regras", response_model=Lista[RegraSaida],
    description="Regras de cada marca, da mais nova para a mais antiga; `vigente` marca a que vale hoje.",
)
def listar_regras(
    marca: str | None = Depends(marca_permitida), db: Session = Depends(get_db), usuario: Usuario = Depends(GERENCIAR),
):
    return {"items": fidelidade.listar_regras(db, marca)}


@router.post(
    "/regras", status_code=status.HTTP_201_CREATED, response_model=RegraSaida,
    description="Cria a regra de uma marca a partir de uma data futura. Vale dali em diante e nunca muda um "
                "ponto já ganho; uma regra que ainda não começou se corrige com outra para a mesma data.",
)
def criar_regra(
    dados: RegraCriar, db: Session = Depends(get_db), usuario: Usuario = Depends(GERENCIAR),
    vistas: set[str] = Depends(marcas_vistas),
):
    conferir_marca(vistas, {dados.marca})
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
    usuario: Usuario = Depends(exige_permissao("registrar_venda_fisica")), vistas: set[str] = Depends(marcas_vistas),
):
    conferir_marca(vistas, {marca})
    cpf = normalizar_cpf(cpf)
    if not cpf_valido(cpf):
        raise RegraDeNegocio("CPF inválido")
    id_cliente, id_cliente_sem_conta = fidelidade.dono_pelo_cpf(db, cpf)
    return fidelidade.simular_resgate(db, id_cliente, id_cliente_sem_conta, marca, pontos)


# ---------- saldo e extrato ----------

# quem consulta os pontos de um cliente: o atendimento ("meus pontos sumiram"), o caixa e quem cuida do programa
CONSULTAR = exige_permissao("atender_chamado", "registrar_venda_fisica", "gerenciar_fidelidade")


@router.get("/meus-pontos", response_model=PontosDoCliente,
            description="Saldo do cliente logado em cada marca, com a categoria e o próximo vencimento.")
def meus_pontos(cliente: Usuario = Depends(exige_cliente), db: Session = Depends(get_db)):
    return fidelidade.pontos_do_cliente(db, id_cliente=cliente.id_usuario)


@router.get("/meus-pontos/extrato", response_model=Pagina[MovimentoPontosSaida],
            description="Extrato do cliente logado, do mais novo para o mais antigo, com o histórico anterior à conta.")
def meu_extrato(
    marca: Marca | None = Query(None), limit: int = Query(LIMITE_PADRAO, ge=1, le=LIMITE_MAXIMO),
    offset: int = Query(0, ge=0), cliente: Usuario = Depends(exige_cliente), db: Session = Depends(get_db),
):
    return fidelidade.extrato(db, limit, offset, marca, id_cliente=cliente.id_usuario)


@router.get("/clientes/pontos", response_model=PontosDoCliente,
            description="Saldo de um cliente com conta (`id_cliente`) ou da Vulto sem conta (`id_cliente_sem_conta`).")
def pontos_de_um_cliente(
    id_cliente: uuid.UUID | None = Query(None), id_cliente_sem_conta: int | None = Query(None),
    db: Session = Depends(get_db), usuario: Usuario = Depends(CONSULTAR), vistas: set[str] = Depends(marcas_vistas),
):
    _um_cliente(id_cliente, id_cliente_sem_conta)
    pontos = fidelidade.pontos_do_cliente(db, id_cliente, id_cliente_sem_conta)
    return {"saldos": [saldo for saldo in pontos["saldos"] if saldo["marca"] in vistas]}


@router.get("/clientes/pontos/extrato", response_model=Pagina[MovimentoPontosSaida],
            description="Extrato de um cliente, como o cliente vê.")
def extrato_de_um_cliente(
    id_cliente: uuid.UUID | None = Query(None), id_cliente_sem_conta: int | None = Query(None),
    marca: str | None = Depends(marca_permitida), limit: int = Query(LIMITE_PADRAO, ge=1, le=LIMITE_MAXIMO),
    offset: int = Query(0, ge=0), db: Session = Depends(get_db), usuario: Usuario = Depends(CONSULTAR),
):
    _um_cliente(id_cliente, id_cliente_sem_conta)
    return fidelidade.extrato(db, limit, offset, marca, id_cliente, id_cliente_sem_conta)


@router.get("/passivo", response_model=Lista[PassivoSaida],
            description="Quanto cada marca deve em pontos, separado pelo valor do ponto na época em que foi ganho.")
def passivo(
    em: str | None = Query(None, description="AAAA-MM-DD (fim do dia) ou AAAA-MM-DDTHH:MM, no horário de Brasília. "
                                             "Ex.: 2026-09-30 para a posição em 01/10. Padrão: agora"),
    db: Session = Depends(get_db), usuario: Usuario = Depends(GERENCIAR), vistas: set[str] = Depends(marcas_vistas),
):
    return {"items": [marca for marca in fidelidade.passivo(db, em) if marca["marca"] in vistas]}


def _um_cliente(id_cliente, id_cliente_sem_conta) -> None:
    if (id_cliente is None) == (id_cliente_sem_conta is None):
        raise RegraDeNegocio("Informe id_cliente ou id_cliente_sem_conta")
