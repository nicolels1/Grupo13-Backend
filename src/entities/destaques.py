from pydantic import BaseModel, Field, field_validator

from src.entities.comum import sem_espacos


# o botão do slide só leva para uma página da própria loja: "/loja/produtos?busca=linho", nunca
# "https://..." nem "//outro-site" (que o navegador também trata como outro endereço)
def link_interno(valor: str | None) -> str | None:
    if valor is not None and (not valor.startswith("/") or valor.startswith("//")):
        raise ValueError("O link precisa ser uma página da loja, começando com / (ex.: /loja/produtos)")
    return valor


class DestaqueCriar(BaseModel):
    titulo: str = Field(min_length=2, max_length=80)
    texto: str = Field(min_length=2, max_length=200)
    rotulo_botao: str = Field(min_length=2, max_length=40, description="Texto do botão do slide")
    link: str = Field(max_length=300, description="Página da loja que o botão abre, começando com /")
    ordem: int | None = Field(default=None, ge=1, description="Sem ordem, o slide entra no fim")
    ativo: bool = True

    _limpa = field_validator("titulo", "texto", "rotulo_botao", "link", mode="before")(sem_espacos)
    _link = field_validator("link")(link_interno)


# desativar = mandar ativo false (o slide some da loja e continua no interno); apagar é pelo DELETE
class DestaqueAlterar(BaseModel):
    titulo: str | None = Field(default=None, min_length=2, max_length=80)
    texto: str | None = Field(default=None, min_length=2, max_length=200)
    rotulo_botao: str | None = Field(default=None, min_length=2, max_length=40)
    link: str | None = Field(default=None, max_length=300)
    ordem: int | None = Field(default=None, ge=1)
    ativo: bool | None = None

    _limpa = field_validator("titulo", "texto", "rotulo_botao", "link", mode="before")(sem_espacos)
    _link = field_validator("link")(link_interno)


class DestaqueSaida(BaseModel):
    id_destaque: int
    titulo: str
    texto: str
    rotulo_botao: str
    link: str
    imagem_url: str | None = Field(description="Foto do slide; vazio sem foto (bloco de cor)")
    ordem: int
    ativo: bool
