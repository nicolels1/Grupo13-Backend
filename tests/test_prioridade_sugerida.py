import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from src.repositories import atendimento_repository
from src.use_cases import atendimento, prioridade_sugerida
from src.utils import modelo_ia

CLIENTE = SimpleNamespace(id_usuario=uuid.UUID("22222222-2222-2222-2222-222222222222"), nome="Bruna Teixeira")


def chamado(**campos):
    base = dict(id_chamado=7, categoria="pagamento", assunto="Cobrança duplicada",
                descricao="Ótimo né, paguei duas vezes e ninguém responde", canal_externo=None, id_pedido=None,
                prioridade_sugerida=None)
    return SimpleNamespace(**{**base, **campos})


class BancoFalso:
    def __init__(self):
        self.updates, self.commits, self.rollbacks, self.fechado = [], 0, 0, False

    def execute(self, consulta):
        self.updates.append(consulta)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.fechado = True


@pytest.fixture
def sugestao(monkeypatch):
    estado = SimpleNamespace(chamado=chamado(), pedido=None, resposta={"prioridade": "alta", "motivo": "cobrança em dobro"},
                             erro=None, textos=[], banco=BancoFalso())

    def classificar(instrucoes, texto, schema):
        estado.textos.append(texto)
        if estado.erro:
            raise estado.erro
        return estado.resposta, None

    m = monkeypatch.setattr
    m(prioridade_sugerida, "SessionLocal", lambda: estado.banco)
    m(atendimento_repository, "buscar_chamado", lambda db, i: estado.chamado if i == 7 else None)
    m(atendimento_repository, "buscar_pedido", lambda db, i: estado.pedido)
    m(modelo_ia, "classificar", classificar)
    return estado


def valores(consulta) -> dict:
    compilado = consulta.compile(dialect=postgresql.dialect())
    return {k: v for k, v in compilado.params.items()}


def test_grava_a_sugestao_sem_mexer_na_prioridade(sugestao):
    prioridade_sugerida.sugerir(7)

    assert sugestao.banco.commits == 1 and sugestao.banco.fechado
    sql = str(sugestao.banco.updates[0].compile(dialect=postgresql.dialect()))
    assert "prioridade_sugerida=" in sql and "motivo_sugerido=" in sql and "sugerida_em=" in sql
    assert " prioridade=" not in sql.replace("prioridade_sugerida=", "")
    # só grava se ainda não houver sugestão; atualizado_em fica como estava
    assert "chamado.prioridade_sugerida IS NULL" in sql and "atualizado_em=chamado.atualizado_em" in sql
    params = valores(sugestao.banco.updates[0])
    assert params["prioridade_sugerida"] == "alta" and params["motivo_sugerido"] == "cobrança em dobro"


def test_o_modelo_le_o_chamado_e_o_pedido_sem_o_nome_do_cliente(sugestao):
    sugestao.chamado = chamado(id_pedido=3, canal_externo="chat")
    sugestao.pedido = SimpleNamespace(status="pago", canal_venda="site", valor_total=Decimal("349.90"),
                                      pago_em=datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc))

    prioridade_sugerida.sugerir(7)

    texto = sugestao.textos[0]
    assert "Categoria: pagamento" in texto and "Assunto: Cobrança duplicada" in texto
    assert "Pedido ligado: status pago, canal site, total R$ 349.90, pago em 27/10/2026" in texto
    assert "Aberto pelo canal: chat" in texto
    assert "Bruna" not in texto


def test_motivo_longo_e_cortado(sugestao):
    sugestao.resposta = {"prioridade": "media", "motivo": "x" * 300}
    prioridade_sugerida.sugerir(7)
    assert len(valores(sugestao.banco.updates[0])["motivo_sugerido"]) == prioridade_sugerida.TAMANHO_DO_MOTIVO


@pytest.mark.parametrize("caso", ["fora_da_lista", "modelo_fora", "ja_sugerida", "sem_chamado"])
def test_sem_sugestao_o_chamado_fica_como_esta(sugestao, caso):
    if caso == "fora_da_lista":
        sugestao.resposta = {"prioridade": "urgente", "motivo": "?"}
    if caso == "modelo_fora":
        sugestao.erro = modelo_ia.ErroModelo("Modelo sem resposta")
    if caso == "ja_sugerida":
        sugestao.chamado = chamado(prioridade_sugerida="baixa")

    prioridade_sugerida.sugerir(99 if caso == "sem_chamado" else 7)

    assert sugestao.banco.updates == [] and sugestao.banco.commits == 0 and sugestao.banco.fechado
    if caso in ("ja_sugerida", "sem_chamado"):
        assert sugestao.textos == []


def test_erro_inesperado_nao_derruba_nada(sugestao, monkeypatch):
    monkeypatch.setattr(sugestao.banco, "execute", lambda consulta: (_ for _ in ()).throw(RuntimeError("banco")))
    prioridade_sugerida.sugerir(7)
    assert sugestao.banco.rollbacks == 1 and sugestao.banco.fechado


def test_abrir_chamado_agenda_a_sugestao_depois_de_gravar(monkeypatch, sem_prioridade_sugerida_em_segundo_plano):
    class Sessao:
        commits = 0

        def add(self, objeto):
            objeto.id_chamado = 41

        def flush(self):
            pass

        def commit(self):
            Sessao.commits += 1

    monkeypatch.setattr(atendimento_repository, "detalhar_chamado", lambda db, lado, i: {"id_chamado": i})

    r = atendimento.abrir_chamado(Sessao(), CLIENTE, {"categoria": "duvida", "assunto": "Prazo", "descricao": "?"})

    assert r == {"id_chamado": 41}
    assert Sessao.commits == 1 and sem_prioridade_sugerida_em_segundo_plano == [41]


# ---------- a equipe vê a sugestão; o cliente não ----------

def test_so_as_rotas_da_equipe_mostram_a_sugestao():
    from src.app import app

    caminhos = app.openapi()["paths"]

    def esquema(caminho, metodo="get"):
        conteudo = caminhos[caminho][metodo]["responses"]
        codigo = "201" if "201" in conteudo else "200"
        return conteudo[codigo]["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[1]

    assert esquema("/chamados/{id_chamado}") == "ChamadoSaida"
    assert esquema("/chamados", "post") == "ChamadoSaida"
    assert esquema("/atendimento/chamados/{id_chamado}") == "ChamadoEquipe"
    assert esquema("/atendimento/chamados/{id_chamado}/assumir", "post") == "ChamadoEquipe"
    assert esquema("/atendimento/chamados") == "Pagina_ChamadoEquipe_"
    componentes = app.openapi()["components"]["schemas"]
    assert "prioridade_sugerida" not in componentes["ChamadoSaida"]["properties"]
    assert "prioridade_sugerida" in componentes["ChamadoEquipe"]["properties"]


def ordem(lado: str) -> str:
    consulta = atendimento_repository.consulta_chamados(lado)
    return str(consulta.compile(dialect=postgresql.dialect())).split("ORDER BY", 1)[1]


def test_fila_da_equipe_poe_a_prioridade_e_depois_quem_espera_mais():
    texto = ordem("equipe")
    # abertos antes dos concluídos; a prioridade definida ou, sem ela, a sugerida; entre os abertos, o mais antigo
    assert texto.index("CASE WHEN (chamado.status = ") < texto.index("coalesce(chamado.prioridade, chamado.prioridade_sugerida)")
    assert "CASE WHEN (chamado.status != " in texto and "THEN chamado.criado_em END ASC" in texto


def test_cliente_ve_os_mais_novos_primeiro():
    assert ordem("cliente").strip() == "chamado.criado_em DESC, chamado.id_chamado DESC"


# ---------- filtro pela prioridade que vale ----------

def onde(**filtros) -> str:
    consulta = atendimento_repository.consulta_chamados("equipe", **filtros)
    return str(consulta.compile(dialect=postgresql.dialect())).split("WHERE", 1)[1].split("ORDER BY", 1)[0]


def test_filtro_de_prioridade_olha_a_definida_ou_a_sugerida():
    assert "coalesce(chamado.prioridade, chamado.prioridade_sugerida) = " in onde(prioridade="alta")
    assert "coalesce(chamado.prioridade, chamado.prioridade_sugerida) IS NULL" in onde(prioridade="sem")


def test_rota_da_fila_aceita_sem_e_recusa_prioridade_que_nao_existe(monkeypatch):
    from fastapi.testclient import TestClient

    from src.app import app
    from src.database.session import get_db
    from src.dependencies.permissoes import marca_permitida
    from src.routes.atendimento import ATENDER

    filtros = []
    monkeypatch.setattr(atendimento, "listar_fila", lambda db, u, limit, offset, **f: filtros.append(f) or {
        "items": [], "total": 0, "limit": limit, "offset": offset})
    app.dependency_overrides[ATENDER] = lambda: CLIENTE
    app.dependency_overrides[marca_permitida] = lambda: None
    app.dependency_overrides[get_db] = lambda: None
    try:
        cliente = TestClient(app)
        sem = cliente.get("/atendimento/chamados", params={"prioridade": "sem"})
        invalida = cliente.get("/atendimento/chamados", params={"prioridade": "urgente"})
    finally:
        app.dependency_overrides.clear()

    assert sem.status_code == 200 and filtros[0]["prioridade"] == "sem"
    assert invalida.status_code == 422
