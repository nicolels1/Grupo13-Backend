from pydantic import BaseModel, Field


class AnexoLink(BaseModel):
    url: str = Field(description="Link temporário do arquivo na área privada do Storage")
    nome: str | None
    expira_em_segundos: int
