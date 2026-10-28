import logging
from dataclasses import dataclass

import anthropic

from src.config import settings

logger = logging.getLogger(__name__)

# o único lugar do backend que chama o modelo (assistente interno): trocar de modelo ou de fornecedor muda só
# aqui. O modelo vem de settings.MODELO_INTERNO
TEMPO_LIMITE = 30  # segundos por chamada
TENTATIVAS = 1  # nova tentativa automática em erro de conexão, 429 ou 5xx
MAX_TOKENS = 2000
# o cálculo é do banco: o modelo só escolhe a ferramenta e escreve a resposta, então pensa pouco
ESFORCO = "low"


class ErroModelo(Exception):
    pass


@dataclass
class ChamadaFerramenta:
    id: str
    nome: str
    parametros: dict


@dataclass
class RespostaModelo:
    conteudo: list  # os blocos da resposta, devolvidos como vieram na rodada seguinte
    texto: str
    chamadas: list[ChamadaFerramenta]
    parou_por: str  # end_turn, tool_use, max_tokens, refusal...
    modelo: str
    tokens_entrada: int = 0
    tokens_saida: int = 0
    tokens_cache_lidos: int = 0
    tokens_cache_escritos: int = 0


_cliente: anthropic.Anthropic | None = None


def _cliente_anthropic() -> anthropic.Anthropic:
    global _cliente
    if not settings.ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY não configurada: assistente interno fora do ar")
        raise ErroModelo("Assistente não configurado")
    if _cliente is None:
        _cliente = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=TEMPO_LIMITE, max_retries=TENTATIVAS)
    return _cliente


# uma rodada: as instruções e as ferramentas ficam em cache (mudam pouco), as mensagens não
def conversar(instrucoes: str, mensagens: list, ferramentas: list[dict]) -> RespostaModelo:
    try:
        resposta = _cliente_anthropic().messages.create(
            model=settings.MODELO_INTERNO,
            max_tokens=MAX_TOKENS,
            system=[{"type": "text", "text": instrucoes, "cache_control": {"type": "ephemeral"}}],
            tools=ferramentas,
            messages=mensagens,
            output_config={"effort": ESFORCO},
        )
    except anthropic.APIError as erro:
        logger.error("modelo sem resposta: %s", erro)
        raise ErroModelo("Modelo sem resposta") from erro

    uso = resposta.usage
    return RespostaModelo(
        conteudo=resposta.content,
        texto="".join(b.text for b in resposta.content if b.type == "text").strip(),
        chamadas=[ChamadaFerramenta(b.id, b.name, dict(b.input)) for b in resposta.content if b.type == "tool_use"],
        parou_por=resposta.stop_reason or "",
        modelo=resposta.model,
        tokens_entrada=uso.input_tokens or 0,
        tokens_saida=uso.output_tokens or 0,
        tokens_cache_lidos=uso.cache_read_input_tokens or 0,
        tokens_cache_escritos=uso.cache_creation_input_tokens or 0,
    )
