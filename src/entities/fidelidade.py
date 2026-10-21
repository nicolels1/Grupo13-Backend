import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.entities.comum import Marca


# ---------- entrada ----------

# regra nova de uma marca, a partir de uma data futura (P10); vale dali em diante e nunca muda o ponto já ganho
class RegraCriar(BaseModel):
    marca: Marca
    valida_desde: date = Field(description="A partir de amanhã; na mesma data, vale a regra criada por último")
    valor_ponto: Decimal = Field(gt=0, max_digits=6, decimal_places=4, description="Em R$ (0,05 = 5 centavos)")
    pontos_por_real: Decimal = Field(ge=0, max_digits=6, decimal_places=2)
    multiplicador_online: Decimal = Field(ge=1, max_digits=4, decimal_places=2, description="Site e app; 2 = dobro")
    bonus_ouro: Decimal = Field(ge=0, max_digits=4, decimal_places=2, description="0,5 = +50% no crédito do Ouro")
    validade_meses: int | None = Field(default=None, gt=0, description="Vazio: o ponto não vence")
    validade_meses_ouro: int | None = Field(default=None, gt=0)
    limite_prata: int = Field(gt=0, description="Pontos ganhos no ano para subir para Prata")
    limite_ouro: int = Field(gt=0, description="Pontos ganhos no ano para subir para Ouro")
    resgate_minimo: int = Field(gt=0)

    @model_validator(mode="after")
    def regras_coerentes(self):
        if (self.validade_meses is None) != (self.validade_meses_ouro is None):
            raise ValueError("Informe a validade das duas categorias, ou de nenhuma")
        if self.validade_meses is not None and self.validade_meses_ouro < self.validade_meses:
            raise ValueError("A validade no Ouro não pode ser menor que a das outras categorias")
        if self.limite_prata >= self.limite_ouro:
            raise ValueError("O limite do Ouro precisa ser maior que o da Prata")
        return self


# ---------- saída ----------

class RegraSaida(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_regra: int
    marca: str
    valida_desde: date
    valor_ponto: Decimal
    pontos_por_real: Decimal
    multiplicador_online: Decimal
    bonus_ouro: Decimal
    validade_meses: int | None
    validade_meses_ouro: int | None
    limite_prata: int
    limite_ouro: int
    resgate_minimo: int
    id_criado_por: uuid.UUID | None = Field(description="Vazio nas regras trazidas pela migração")
    criado_em: datetime
    vigente: bool = Field(description="É a regra que vale hoje para a marca")


# quanto vale um resgate antes de fechar a compra (o caixa e o site mostram o desconto)
class ResgateSimulado(BaseModel):
    pontos: int
    desconto: Decimal = Field(description="Em R$: os pontos mais antigos primeiro, cada um pelo valor da época")
    saldo: int = Field(description="Saldo de pontos do cliente na marca")
    resgate_minimo: int


class SaldoPontosSaida(BaseModel):
    marca: str
    saldo: int
    valor_em_reais: Decimal = Field(description="O que os pontos valem, cada um pelo valor da época em que foi ganho")
    categoria: str = Field(description="bronze, prata ou ouro")
    categoria_desde: datetime
    ganhos_no_ano: int = Field(description="Pontos ganhos no ano: decidem a categoria")
    proximo_vencimento: datetime | None
    pontos_no_proximo_vencimento: int


class MovimentoPontosSaida(BaseModel):
    id_movimento: int
    marca: str
    tipo: str = Field(description="saldo_inicial, credito, resgate, estorno, ajuste ou vencimento")
    pontos: int = Field(description="Positivo entra, negativo sai")
    origem: str = Field(description="migracao (histórico da Vulto) ou plataforma")
    id_pedido: int | None
    codigo_venda: str | None
    id_movimento_original: int | None
    motivo: str | None
    criado_em: datetime
    ganho_em: datetime | None = Field(description="Crédito de uma compra de antes do cadastro: a data da compra")
    restante: int | None = Field(description="Entradas: quantos pontos dela ainda sobram")
    vence_em: datetime | None = Field(description="Entradas: quando o que sobra vence")


class PontosDoCliente(BaseModel):
    saldos: list[SaldoPontosSaida]


class PassivoPorValor(BaseModel):
    valor_ponto: Decimal
    pontos: int
    reais: Decimal


class PassivoSaida(BaseModel):
    marca: str
    pontos: int
    reais: Decimal = Field(description="Cada ponto pelo valor da época em que foi ganho (K3)")
    por_valor: list[PassivoPorValor]
