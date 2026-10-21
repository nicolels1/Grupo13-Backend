from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from src.entities.comum import Marca
from src.entities.fidelidade import PassivoSaida

APP = "Só quando o período tem venda pelo app, que existe só no histórico da Vulto"


class Volume(BaseModel):
    pedidos: int
    valor: Decimal


# por canal da venda (P3)
class VendasDoDia(BaseModel):
    dia: date
    loja: Volume
    site: Volume
    app: Volume | None = Field(default=None, description=APP)


class MaisVendida(BaseModel):
    id_variante: int
    produto: str
    cor: str
    tamanho: str
    sku: str
    foto_url: str | None
    quantidade_vendida: int
    saldo_atual: int = Field(description="Loja física + online, na unidade filtrada ou na rede")


class ChamadosDoDia(BaseModel):
    dia: date
    abertos: int
    concluidos: int


class Chamados(BaseModel):
    abertos: int
    em_andamento: int
    concluidos_7_dias: int
    por_dia: list[ChamadosDoDia]


class VolumeComVariacao(Volume):
    variacao_pedidos_pct: float | None = Field(description="Contra os 14 dias anteriores; vazio sem base")
    variacao_valor_pct: float | None


class VendasPorCanal(BaseModel):
    loja: VolumeComVariacao
    site: VolumeComVariacao
    app: VolumeComVariacao | None = Field(default=None, description=APP)
    total: VolumeComVariacao


class TicketPorCanal(BaseModel):
    loja: Decimal | None
    site: Decimal | None
    app: Decimal | None = Field(default=None, description=APP)
    total: Decimal | None


class VendasDaMarca(BaseModel):
    marca: Marca
    vendas_14_dias: VolumeComVariacao
    ticket_medio_30_dias: Decimal | None


class ResumoAvaliacoes(BaseModel):
    nota_media_90_dias: float | None
    quantidade_90_dias: int
    denuncias_pendentes: int


class RedeAgora(BaseModel):
    cobertura_dias: float | None = Field(description="Saldo da rede ÷ média diária de peças vendidas em 30 dias")
    ruptura_online_pct: float | None = Field(description="% das variantes à venda sem peça disponível online")
    vendas_14_dias: VendasPorCanal
    ticket_medio_30_dias: TicketPorCanal
    por_marca: list[VendasDaMarca] = Field(description="Casa Lorenzi × Vulto; só a marca filtrada, se houver")
    primeira_resposta_mediana_horas: float | None
    chamados_esperando_primeira_resposta: int
    avaliacoes: ResumoAvaliacoes
    retiradas_perto_de_vencer: int = Field(description="Prontas para retirada há mais de 5 dias")


class LinhaDaUnidade(BaseModel):
    id_unidade: int
    nome: str
    tipo: str
    marca: Marca
    vendas_7_dias: Volume
    cobertura_dias: float | None
    variantes_abaixo_do_minimo: int
    retiradas_perto_de_vencer: int | None = Field(description="Vazio no CD, que não faz retirada")
    transferencias_esperando_envio: int
    transferencias_chegando: int


class LinhaDoFaturamento(BaseModel):
    mes: date = Field(description="Primeiro dia do mês, no horário de Brasília")
    id_unidade: int
    unidade: str
    canal_venda: str = Field(description="loja, site ou app (P3)")
    pedidos: int = Field(description="Pedidos pagos no mês; a devolução não conta pedido")
    valor: Decimal = Field(description="Sem o frete, menos as devoluções no mês em que saíram")
    frete: Decimal = Field(description="À parte (V7)")


class Faturamento(BaseModel):
    de: date
    ate: date
    id_unidade: int | None
    marca: Marca | None
    valor: Decimal
    frete: Decimal
    items: list[LinhaDoFaturamento]


# cada seção só vem para quem pode vê-la; as outras ficam ausentes da resposta
class Resumo(BaseModel):
    id_unidade: int | None
    marca: Marca | None
    referencia: date = Field(description="Último dia das janelas de 7, 14, 30 e 90 dias")
    gerado_em: datetime
    vendas_por_dia: list[VendasDoDia] | None = Field(
        default=None, description="Vendas ou Admin: 14 dias por canal da venda, pelo dia do pagamento, sem cancelados")
    mais_vendidas: list[MaisVendida] | None = Field(default=None, description="Vendas ou Admin: top 5 em 7 dias")
    chamados: Chamados | None = Field(default=None, description="atender_chamado ou Admin")
    pontos: list[PassivoSaida] | None = Field(
        default=None, description="gerenciar_fidelidade ou Admin: o que cada marca deve em pontos hoje")
    rede_agora: RedeAgora | None = Field(default=None, description="Só Admin; a rede inteira ou a marca filtrada")
    por_unidade: list[LinhaDaUnidade] | None = Field(default=None, description="Só Admin")
