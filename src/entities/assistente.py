from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class PecaSaida(BaseModel):
    id_produto: int
    nome: str
    marca: str
    descricao: str
    preco_a_partir: Decimal
    cores: list[str]
    tamanhos_no_site: list[str]
    foto: str | None
    link: str


class BuscaPecasSaida(BaseModel):
    items: list[PecaSaida]
    # exato: achou o que foi pedido; parecidas ou novidades: não achou e mostra outra coisa
    resultado: Literal["exato", "parecidas", "novidades"]


# quantidade só quando está acabando (até 3); acima disso, só que tem
class FaixaEstoque(BaseModel):
    tem: bool
    quantidade: int | None
    acabando: bool


class EstoqueNaLoja(FaixaEstoque):
    id_unidade: int
    nome: str
    endereco: str


class EstoqueSaida(BaseModel):
    id_variante: int
    produto: str
    marca: str
    cor: str
    tamanho: str
    preco: Decimal
    site: FaixaEstoque
    lojas: list[EstoqueNaLoja]


class LinkCarrinhoSaida(BaseModel):
    id_variante: int
    produto: str
    cor: str
    tamanho: str
    link: str


class HorarioHoje(BaseModel):
    aberta_agora: bool
    texto: str


class HorarioDoDia(BaseModel):
    dia: str
    horario: str


class LojaSaida(BaseModel):
    id_unidade: int
    nome: str
    endereco: str
    hoje: HorarioHoje
    semana: list[HorarioDoDia]


class LojasDaMarca(BaseModel):
    marca: str
    lojas: list[LojaSaida]


class VocabularioSaida(BaseModel):
    categorias: list[str]
    cores: list[str]


# ---------- conversa e ferramentas com login ----------

# a conversa vem do canal (telefone do WhatsApp ou sessão do chat), tirada pelo n8n; nunca do modelo.
# Vai no corpo, não na URL, para o telefone não aparecer no log de acesso
class ConversaEntrada(BaseModel):
    canal: Literal["whatsapp", "chat"]
    id_externo: str = Field(min_length=1, max_length=100)


class EtapaEntrada(ConversaEntrada):
    etapa: Literal["livre", "esperando_cpf"]


class CpfEntrada(ConversaEntrada):
    cpf: str = Field(min_length=11, max_length=20, description="Com ou sem pontos e traço")


class CodigoEntrada(ConversaEntrada):
    codigo: str = Field(min_length=1, max_length=10)


class PedidosEntrada(ConversaEntrada):
    id_pedido: int | None = Field(default=None, description="Um pedido só; vazio traz os últimos 5")


class EstadoConversa(BaseModel):
    etapa: str
    confirmado: bool = Field(description="Conta confirmada pelo código nesta conversa")
    conversa_nova: bool = Field(description="Passou 30 min da última mensagem: apagar a memória curta")
    limite_atingido: bool = Field(description="Passou das 100 mensagens hoje: não responder")
    avisar_limite: bool = Field(description="Primeira mensagem acima do limite: mandar o aviso uma vez")
    bloqueado_ate: datetime | None = Field(description="Confirmação pausada por código errado")


class CodigoEnviado(BaseModel):
    mensagem: str = Field(description="Igual para CPF com e sem cadastro")
    validade_minutos: int


class ConfirmacaoSaida(BaseModel):
    confirmado: bool
    nome: str | None = Field(description="Primeiro nome, para cumprimentar")


class Vencimento(BaseModel):
    data: date
    pontos: int


class PontosDaMarca(BaseModel):
    marca: str
    saldo: int
    valor_em_reais: Decimal
    categoria: str
    proxima_categoria: str | None
    faltam_para_subir: int | None = Field(description="Pontos a ganhar no ano para subir de categoria")
    proximo_vencimento: Vencimento | None
    pontos_sem_vencimento: int
    texto: str


class MeusPontosSaida(BaseModel):
    marcas: list[PontosDaMarca]
    texto: str = Field(description="Resposta pronta, para quando a da IA não bater com os números")


class ItemDoPedido(BaseModel):
    produto: str
    cor: str
    tamanho: str
    quantidade: int


class RetiradaDoPedido(BaseModel):
    loja: str
    endereco: str
    retirar_ate: date | None = Field(description="Só quando já está pronto: 7 dias depois")


class PedidoDoCliente(BaseModel):
    id_pedido: int
    codigo_venda: str
    marca: str
    feito_em: date
    na_loja: bool
    modalidade: str | None
    status: str
    valor_total: Decimal
    itens: list[ItemDoPedido]
    retirada: RetiradaDoPedido | None
    pagar_ate: datetime | None
    texto: str


class MeusPedidosSaida(BaseModel):
    items: list[PedidoDoCliente]
    texto: str = Field(description="Resposta pronta, para quando a da IA não bater com os números")
