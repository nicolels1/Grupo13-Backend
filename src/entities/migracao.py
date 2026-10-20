from pydantic import BaseModel, ConfigDict, Field


class OrigemSaida(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    fonte: str = Field(description="Arquivo da Vulto: vulto.db ou o nome da planilha")
    linha_origem: str = Field(description="Linha na fonte, como no staging: vendas#272, Clientes!15")
    campo: str | None = Field(description="Vazio: a linha só diz de onde o registro veio")
    valor_original: str | None = Field(description="Valor do campo na Vulto, antes da migração mudar")
    decisao: str | None = Field(description="Número no registro de decisões da migração (ex.: K2)")
