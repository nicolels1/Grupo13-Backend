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
