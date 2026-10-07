from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class Volume(BaseModel):
    pedidos: int
    valor: Decimal


class VendasDoDia(BaseModel):
    dia: date
    online: Volume
    loja_fisica: Volume


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
    online: VolumeComVariacao
    loja_fisica: VolumeComVariacao
    total: VolumeComVariacao


class TicketPorCanal(BaseModel):
    online: Decimal | None
    loja_fisica: Decimal | None
    total: Decimal | None


class ResumoAvaliacoes(BaseModel):
    nota_media_90_dias: float | None
    quantidade_90_dias: int
    denuncias_pendentes: int


class RedeAgora(BaseModel):
    cobertura_dias: float | None = Field(description="Saldo da rede ÷ média diária de peças vendidas em 30 dias")
    ruptura_online_pct: float | None = Field(description="% das variantes à venda sem peça disponível online")
    vendas_14_dias: VendasPorCanal
    ticket_medio_30_dias: TicketPorCanal
    primeira_resposta_mediana_horas: float | None
    chamados_esperando_primeira_resposta: int
    avaliacoes: ResumoAvaliacoes
    retiradas_perto_de_vencer: int = Field(description="Prontas para retirada há mais de 5 dias")


class LinhaDaUnidade(BaseModel):
    id_unidade: int
    nome: str
    tipo: str
    vendas_7_dias: Volume
    cobertura_dias: float | None
    variantes_abaixo_do_minimo: int
    retiradas_perto_de_vencer: int | None = Field(description="Vazio no CD, que não faz retirada")
    transferencias_esperando_envio: int
    transferencias_chegando: int


# cada seção só vem para quem pode vê-la; as outras ficam ausentes da resposta
class Resumo(BaseModel):
    id_unidade: int | None
    gerado_em: datetime
    vendas_por_dia: list[VendasDoDia] | None = Field(
        default=None, description="Vendas ou Admin: 14 dias, pelo dia do pagamento, sem cancelados")
    mais_vendidas: list[MaisVendida] | None = Field(default=None, description="Vendas ou Admin: top 5 em 7 dias")
    chamados: Chamados | None = Field(default=None, description="atender_chamado ou Admin")
    rede_agora: RedeAgora | None = Field(default=None, description="Só Admin; sempre a rede inteira")
    por_unidade: list[LinhaDaUnidade] | None = Field(default=None, description="Só Admin")
