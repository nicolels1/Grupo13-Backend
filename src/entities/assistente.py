from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from src.entities.atendimento import Categoria
from src.entities.comum import sem_espacos


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


class PecaDoLink(BaseModel):
    id_variante: int
    produto: str
    cor: str
    tamanho: str


class PecaForaDoLink(BaseModel):
    id_variante: int
    motivo: str = Field(description="Sem estoque para o site (diz as lojas que têm) ou peça não encontrada")


class LinkCarrinhoSaida(BaseModel):
    items: list[PecaDoLink] = Field(description="As peças que o link põe no carrinho")
    fora_do_link: list[PecaForaDoLink] = Field(description="As peças que não deu para pôr no carrinho")
    link: str


class HorarioHoje(BaseModel):
    aberta_agora: bool
    texto: str


class HorarioDoDia(BaseModel):
    data: date
    dia: str
    horario: str = Field(description="\"10h às 20h\" ou \"fechada\"")
    motivo: str | None = Field(description="Exceção cadastrada pela loja (ex.: Natal) ou o feriado nacional")
    aviso: str | None = Field(description="Feriado sem exceção cadastrada: o horário pode mudar")


class LojaSaida(BaseModel):
    id_unidade: int
    nome: str
    endereco: str
    hoje: HorarioHoje
    proximos_dias: list[HorarioDoDia] = Field(description="Hoje e os 6 dias seguintes")


class LojasDaMarca(BaseModel):
    marca: str
    lojas: list[LojaSaida]


class VocabularioSaida(BaseModel):
    categorias: list[str]
    cores: list[str]


# ---------- conversa e ferramentas com login ----------

# a conversa vem do canal (telefone do WhatsApp ou chat_id do Telegram), tirada pelo n8n;
# nunca do modelo. Vai no corpo, não na URL, para o telefone não aparecer no log de acesso
class ConversaEntrada(BaseModel):
    canal: Literal["whatsapp", "telegram"]
    id_externo: str = Field(min_length=1, max_length=100)


class EtapaEntrada(ConversaEntrada):
    etapa: Literal["livre", "esperando_cpf"]


class CpfEntrada(ConversaEntrada):
    cpf: str = Field(min_length=11, max_length=20, description="Com ou sem pontos e traço")


class CodigoEntrada(ConversaEntrada):
    codigo: str = Field(min_length=1, max_length=10)


class PedidosEntrada(ConversaEntrada):
    id_pedido: int | None = Field(default=None, description="Um pedido só; vazio traz os últimos 5")


# o que só o n8n sabe da conversa, para o resumo anônimo; os dois campos são opcionais
class ResumoDoN8nEntrada(ConversaEntrada):
    avaliacao: Literal["positiva", "negativa"] | None = Field(default=None, description="👍 ou 👎 do cliente")
    fora_do_tema: bool = Field(default=False, description="O cliente falou de algo fora do que o assistente atende")


class FalaDaConversa(BaseModel):
    de: Literal["cliente", "assistente"]
    texto: str = Field(min_length=1, max_length=1000)


# o resumo que a IA montou; fica guardado até o cliente apertar [Enviar] ou [Corrigir]
class RascunhoDoChamadoEntrada(ConversaEntrada):
    categoria: Categoria
    assunto: str = Field(min_length=3, max_length=200)
    resumo: str = Field(min_length=1, max_length=2000, description="O que vai para a equipe, para o cliente conferir")
    id_pedido: int | None = Field(default=None, description="Pedido do próprio cliente de que o chamado trata")

    _limpa = field_validator("assunto", "resumo", mode="before")(sem_espacos)


class RascunhoSaida(BaseModel):
    mensagem: str = Field(description="Texto pronto para mostrar com os botões [Enviar] [Corrigir]")


# depois do botão [Enviar]: o chamado abre com o rascunho guardado; a conversa vem da memória curta do n8n,
# para o atendente saber o que o cliente já falou
class ChamadoDoAssistenteEntrada(ConversaEntrada):
    conversa: list[FalaDaConversa] = Field(default_factory=list, max_length=40,
                                           description="As últimas mensagens, da mais antiga para a mais nova")


class MensagemNoChamadoEntrada(ConversaEntrada):
    texto: str = Field(min_length=1, max_length=5000)

    _limpa = field_validator("texto", mode="before")(sem_espacos)


# depois de cada resposta, o que o cliente mandou e o que o assistente respondeu; CPF e código saem mascarados
class TextoDaConversaEntrada(ConversaEntrada):
    cliente: str = Field(min_length=1, max_length=5000)
    assistente: str | None = Field(default=None, max_length=5000,
                                   description="Vazio quando não houve resposta (ex.: limite do dia)")

    _limpa = field_validator("cliente", "assistente", mode="before")(sem_espacos)


class LembreteEntrada(ConversaEntrada):
    aceita: bool = Field(description="Aceitou (true) ou recusou (false) o lembrete antes de os pontos vencerem")


class LembreteSaida(BaseModel):
    aceita: bool
    mensagem: str = Field(description="Texto pronto para o cliente")


class AvisoPontosSaida(BaseModel):
    clientes_avisados: int
    clientes_com_falha: int = Field(description="O e-mail não saiu: os lotes voltam no aviso do dia seguinte")


class EstadoConversa(BaseModel):
    etapa: str
    confirmado: bool = Field(description="Conta confirmada pelo código nesta conversa")
    conversa_nova: bool = Field(description="Passou 30 min da última mensagem: apagar a memória curta")
    limite_atingido: bool = Field(description="Passou das 100 mensagens hoje: não responder")
    avisar_limite: bool = Field(description="Primeira mensagem acima do limite: mandar o aviso uma vez")
    bloqueado_ate: datetime | None = Field(description="Confirmação pausada por código errado")
    id_chamado: int | None = Field(description="Chamado aberto pela conversa: a mensagem também vai para ele")
    equipe_disponivel: bool = Field(description="A equipe de atendimento está no horário agora")
    equipe_volta_em: datetime | None = Field(description="Quando a equipe volta; vazio se ela está atendendo")
    aviso_equipe: str | None = Field(description="Fora do horário: texto pronto dizendo quando a equipe volta")


class ChamadoAbertoSaida(BaseModel):
    id_chamado: int
    mensagem: str = Field(description="Texto pronto para o cliente")


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
    oferecer_lembrete: bool = Field(description="Oferecer o e-mail antes de os pontos vencerem (uma vez por conta)")


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
