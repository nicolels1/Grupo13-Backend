import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.entities.comum import Marca, sem_espacos
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
class _Destino(Carrinho):
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


class Checkout(_Destino):
    pontos_resgate: int | None = Field(
        default=None, gt=0, description="Pontos usados como desconto (GET /fidelidade/resgate mostra quanto valem)"
    )


# compra do site (ADR 0017): os pontos de cada marca viram desconto no pedido dela
class CheckoutCompra(_Destino):
    pontos_por_marca: dict[Marca, Annotated[int, Field(gt=0)]] = Field(
        default_factory=dict,
        description="Pontos usados como desconto em cada marca, como {\"lorenzi\": 200}. Cada marca usa os pontos "
                    "dela no pedido dela (GET /fidelidade/resgate mostra quanto valem)",
    )


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
    pontos_resgate: int | None = Field(
        default=None, gt=0,
        description="Pontos do cliente do CPF usados como desconto; os pagamentos fecham com o total menos o desconto",
    )

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
    foto_url: str | None = Field(description="Primeira foto da cor comprada; vazio quando o produto não tem foto")


class PagamentoSaida(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_pagamento: int
    tipo: str
    id_pagamento_original: int | None
    id_chamado: int | None
    origem: str | None = Field(
        description="Estorno: cancelamento, atendimento ou balcao; pagamento e estorno migrados da Vulto: migracao"
    )
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
    id_cliente_sem_conta: int | None = Field(description="Comprador migrado que ainda não criou conta")
    cliente: str | None
    id_unidade: int
    unidade: str
    id_compra: int | None = Field(
        description="Compra do site a que o pedido pertence (paga e cancela pela compra); vazio fora dela"
    )
    id_registrado_por: uuid.UUID | None
    cpf_nota: str | None = Field(description="CPF informado na venda física")
    canal: str = Field(description="Canal do estoque: loja_fisica ou online")
    canal_venda: str = Field(description="Onde a venda foi feita: loja, site ou app (app só no histórico da Vulto)")
    modalidade: str | None
    status: StatusPedido
    motivo_cancelamento: str | None
    id_cancelado_por: uuid.UUID | None
    justificativa_cancelamento: str | None
    devolucao: str
    valor_itens: Valor
    valor_frete: Valor
    valor_desconto: Valor = Field(description="Desconto já tirado do total; só em pedido migrado da Vulto")
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


# compra do site: um checkout e uma cobrança para o cliente, um pedido por marca por dentro (ADR 0017)
class CompraSaida(BaseModel):
    id_compra: int
    codigo_compra: str
    status: Literal["aguardando_pagamento", "pago", "cancelado"]
    valor_itens: Valor
    valor_frete: Valor = Field(description="O frete da compra, um só, repartido entre os pedidos pelo valor das peças")
    valor_desconto: Valor = Field(description="Pontos usados como desconto, somando as marcas")
    valor_total: Valor = Field(description="O que é cobrado: a soma dos pedidos")
    reserva_expira_em: datetime = Field(description="A reserva das peças de todos os pedidos vence junto")
    metodo: MetodoOnline | None = Field(description="Método da cobrança; vazio antes de pedir a cobrança")
    id_transacao_gateway: str | None = Field(description="Transação da cobrança no gateway simulado")
    status_cobranca: Literal["pendente", "aprovado", "recusado"] | None
    pago_em: datetime | None
    cancelado_em: datetime | None
    criado_em: datetime
    pedidos: list[PedidoSaida] = Field(description="Um pedido por marca, cada um saindo de uma unidade dela")


class ItemResumo(BaseModel):
    id_variante: int
    sku: str
    produto: str
    marca: Marca = Field(description="Marca do produto: o carrinho do site junta as duas e o pedido sai por marca")
    cor: str
    tamanho: str
    quantidade: int
    preco_unitario: Valor
    subtotal: Valor


# cada marca vira um pedido próprio, saindo de uma unidade dela (ADR 0017); o frete é um só, da compra
class ResumoMarca(BaseModel):
    marca: Marca
    valor_itens: Valor
    entrega_disponivel: bool = Field(description="Algum CD ou loja que despacha desta marca tem todos os itens dela")


class LojaRetirada(BaseModel):
    id_unidade: int
    nome: str
    cidade: str
    uf: str


class ResumoCarrinho(BaseModel):
    itens: list[ItemResumo]
    valor_itens: Valor
    frete_entrega: Valor = Field(
        description="Um frete só para o carrinho, mesmo com as duas marcas; grátis a partir do valor mínimo; zero na "
                    "retirada"
    )
    frete_gratis_a_partir_de: Valor = Field(
        description="Valor mínimo em itens do carrinho inteiro para o frete sair grátis (a loja mostra quanto falta)"
    )
    total_entrega: Valor
    total_retirada: Valor
    entrega_disponivel: bool = Field(
        description="Cada marca do carrinho tem um CD ou loja que despacha com todos os itens dela"
    )
    lojas_retirada: list[LojaRetirada] = Field(
        description="Lojas com todos os itens no estoque online (vazia com duas marcas: cada loja vende só a sua)"
    )
    marcas: list[ResumoMarca] = Field(description="Uma linha por marca do carrinho: cada uma vira um pedido")


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


# cliente com conta na área Clientes da equipe: o resumo e, se veio da Vulto, o cadastro de origem
class ClienteComConta(ClienteResumo):
    id_cliente_sem_conta: int | None = Field(
        description="Cadastro da Vulto que passou para esta conta (a origem); vazio = não veio da migração",
    )


# cliente da migração da Vulto que ainda não criou conta (ADR 0016); só a equipe consulta
class ClienteSemContaSaida(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_cliente_sem_conta: int
    nome: str | None
    cpf: str | None
    email: str | None
    telefone: str | None
    aceita_mensagem: bool | None = Field(description="Aceita receber mensagens; vazio = não informado")
    origem: str
    id_usuario: uuid.UUID | None = Field(
        description="Conta que recebeu o histórico quando a pessoa se cadastrou; vazio = ainda sem conta",
    )
    criado_em: datetime


class ClienteCorrigido(ClienteResumo):
    id_conta_mantida: uuid.UUID | None = Field(
        default=None, description="Na correção de CPF que já tinha conta: a conta que ficou com os pedidos",
    )
    compras_ligadas: int = Field(description="Compras da loja com esse CPF na nota que passaram para a conta")
