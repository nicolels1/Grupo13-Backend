import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.entities.comum import sem_espacos
from src.entities.contas import PADRAO_EMAIL, _validar_cpf as _cpf

Modalidade = Literal["entrega", "retirada"]
StatusPedido = Literal["aguardando_pagamento", "pago", "enviado", "pronto_para_retirada", "entregue", "cancelado"]
MetodoOnline = Literal["pix", "cartao_credito", "cartao_debito"]
Metodo = Literal["pix", "cartao_credito", "cartao_debito", "dinheiro"]
Valor = Decimal


# ---------- entrada ----------

class ItemCarrinho(BaseModel):
    id_variante: int
    quantidade: int = Field(gt=0, le=99)


class Carrinho(BaseModel):
    itens: list[ItemCarrinho] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def variantes_sem_repeticao(self):
        ids = [i.id_variante for i in self.itens]
        if len(set(ids)) != len(ids):
            raise ValueError("Variante repetida: junte as quantidades")
        return self


# entrega em casa usa um endereço salvo (o pedido guarda a cópia); retirada, uma loja escolhida
class Checkout(Carrinho):
    modalidade: Modalidade
    id_endereco: int | None = Field(default=None, description="Obrigatório na entrega")
    id_unidade_retirada: int | None = Field(default=None, description="Obrigatório na retirada")

    @model_validator(mode="after")
    def destino_da_modalidade(self):
        if self.modalidade == "entrega" and self.id_endereco is None:
            raise ValueError("Entrega em casa precisa de id_endereco")
        if self.modalidade == "retirada" and self.id_unidade_retirada is None:
            raise ValueError("Retirada precisa de id_unidade_retirada")
        return self


class CobrancaCriar(BaseModel):
    metodo: MetodoOnline


# resposta do gateway de pagamento simulado (não há gateway real no case)
class RespostaGateway(BaseModel):
    aprovado: bool


class Reivindicacao(BaseModel):
    codigo_venda: str = Field(min_length=4, max_length=20)

    _limpa = field_validator("codigo_venda", mode="before")(sem_espacos)


class PagamentoFisico(BaseModel):
    metodo: Metodo
    valor: Valor = Field(gt=0, max_digits=10, decimal_places=2)


# venda na loja: nasce paga e entregue. CPF opcional (o vendedor pede, sem exigir). O caixa não
# cria conta: CPF com conta liga o pedido a ela; CPF sem conta fica guardado no pedido (ADR 0014)
class VendaFisica(Carrinho):
    id_unidade: int
    cpf_nota: str | None = Field(default=None, description="CPF na nota, com ou sem máscara")
    pagamentos: list[PagamentoFisico] = Field(min_length=1, max_length=5)

    @field_validator("cpf_nota")
    @classmethod
    def validar_cpf(cls, valor: str | None) -> str | None:
        return _cpf(valor) if valor else None


class CancelamentoEquipe(BaseModel):
    justificativa: str = Field(min_length=3, max_length=500)

    _limpa = field_validator("justificativa", mode="before")(sem_espacos)


# na retirada, a pessoa mostra o código do pedido e um documento
class Entrega(BaseModel):
    codigo_venda: str | None = Field(default=None, description="Obrigatório na retirada na loja")


# ---------- saída ----------

class ItemPedidoSaida(BaseModel):
    id_item: int
    id_variante: int
    sku: str
    produto: str
    cor: str
    tamanho: str
    quantidade: int
    preco_unitario: Valor
    id_avaliacao: int | None = Field(description="Avaliação já feita para este item, se houver")


class PagamentoSaida(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_pagamento: int
    tipo: str
    id_pagamento_original: int | None
    id_chamado: int | None
    origem: str | None = Field(description="Estorno: cancelamento, atendimento ou balcao")
    id_registrado_por: uuid.UUID | None = Field(description="Estorno no balcão: quem registrou")
    id_unidade: int | None = Field(description="Estorno no balcão: a loja")
    metodo: str
    id_transacao_gateway: str | None
    valor: Valor
    status: str
    criado_em: datetime


class EnderecoEntregaSaida(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    rua: str
    numero: str
    complemento: str | None
    bairro: str
    cidade: str
    uf: str
    cep: str


class PedidoSaida(BaseModel):
    id_pedido: int
    codigo_venda: str
    id_cliente: uuid.UUID | None
    cliente: str | None
    id_unidade: int
    unidade: str
    id_registrado_por: uuid.UUID | None
    cpf_nota: str | None = Field(description="CPF informado na venda física")
    canal: str
    modalidade: str | None
    status: StatusPedido
    motivo_cancelamento: str | None
    id_cancelado_por: uuid.UUID | None
    justificativa_cancelamento: str | None
    devolucao: str
    valor_itens: Valor
    valor_frete: Valor
    valor_total: Valor
    valor_pago: Valor = Field(description="Pagamentos aprovados menos estornos aprovados")
    reserva_expira_em: datetime | None
    pago_em: datetime | None
    enviado_em: datetime | None
    pronto_retirada_em: datetime | None
    entregue_em: datetime | None
    cancelado_em: datetime | None
    criado_em: datetime
    itens: list[ItemPedidoSaida]
    pagamentos: list[PagamentoSaida]
    endereco_entrega: EnderecoEntregaSaida | None


class ItemResumo(BaseModel):
    id_variante: int
    sku: str
    produto: str
    cor: str
    tamanho: str
    quantidade: int
    preco_unitario: Valor
    subtotal: Valor


class LojaRetirada(BaseModel):
    id_unidade: int
    nome: str
    cidade: str
    uf: str


class ResumoCarrinho(BaseModel):
    itens: list[ItemResumo]
    valor_itens: Valor
    frete_entrega: Valor = Field(description="Grátis a partir do valor mínimo; zero na retirada")
    total_entrega: Valor
    total_retirada: Valor
    entrega_disponivel: bool = Field(description="Algum CD ou loja que despacha tem todos os itens")
    lojas_retirada: list[LojaRetirada] = Field(description="Lojas com todos os itens no estoque online")


# ---------- clientes no caixa ----------

# correção na loja, com documento: e-mail e/ou CPF
class ClienteCorrigir(BaseModel):
    email: str | None = Field(default=None, pattern=PADRAO_EMAIL, max_length=255)
    cpf: str | None = None

    _limpa = field_validator("email", mode="before")(sem_espacos)

    @field_validator("cpf")
    @classmethod
    def validar_cpf(cls, valor: str | None) -> str | None:
        return _cpf(valor) if valor else None

    @field_validator("email")
    @classmethod
    def email_minusculo(cls, valor: str | None) -> str | None:
        return valor.lower() if valor else valor


class ClienteResumo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_usuario: uuid.UUID
    nome: str
    email: str
    cpf: str | None
    status_conta: str


class ClienteCorrigido(ClienteResumo):
    id_conta_mantida: uuid.UUID | None = Field(
        default=None, description="Na correção de CPF que já tinha conta: a conta que ficou com os pedidos",
    )
    compras_ligadas: int = Field(description="Compras da loja com esse CPF na nota que passaram para a conta")
