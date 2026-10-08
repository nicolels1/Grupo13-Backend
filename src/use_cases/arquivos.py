import logging
import uuid
from dataclasses import dataclass

from src.use_cases.erros import RegraDeNegocio, ServicoIndisponivel
from src.utils.supabase_admin import ErroSupabase
from src.utils.upload import Arquivo

logger = logging.getLogger(__name__)

MB = 1024 * 1024

# assinatura dos primeiros bytes de cada tipo aceito: o tipo informado pelo navegador não basta
ASSINATURAS = {
    "image/jpeg": lambda c: c.startswith(b"\xff\xd8\xff"),
    "image/png": lambda c: c.startswith(b"\x89PNG\r\n\x1a\n"),
    "image/webp": lambda c: c.startswith(b"RIFF") and c[8:12] == b"WEBP",
    "application/pdf": lambda c: c.startswith(b"%PDF"),
}
EXTENSOES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "application/pdf": ".pdf"}
IMAGENS = ("image/jpeg", "image/png", "image/webp")


@dataclass
class Regra:
    bucket: str
    tipos: tuple[str, ...]
    limite: int  # bytes
    descricao: str  # usada na mensagem de erro


# os mesmos limites dos buckets (migration 03aeb347f6cf)
FOTO_PRODUTO = Regra("produtos", IMAGENS, 5 * MB, "JPG, PNG ou WEBP de até 5 MB")
ANEXO_CHAMADO = Regra("anexos", IMAGENS + ("application/pdf",), 10 * MB, "JPG, PNG, WEBP ou PDF de até 10 MB")
FOTO_AVALIACAO = Regra("avaliacoes", IMAGENS, 5 * MB, "JPG, PNG ou WEBP de até 5 MB")


def conferir(regra: Regra, arquivo: Arquivo) -> None:
    if not arquivo.conteudo:
        raise RegraDeNegocio("Arquivo vazio")
    if len(arquivo.conteudo) > regra.limite:
        raise RegraDeNegocio(f"Arquivo grande demais: envie {regra.descricao}")
    if arquivo.tipo not in regra.tipos or not ASSINATURAS[arquivo.tipo](arquivo.conteudo):
        raise RegraDeNegocio(f"Tipo de arquivo não aceito: envie {regra.descricao}")


# pasta do registro + nome aleatório: dois envios nunca se sobrescrevem e o nome original não vaza
def novo_caminho(pasta: str, tipo: str) -> str:
    return f"{pasta}/{uuid.uuid4().hex}{EXTENSOES[tipo]}"


# nenhuma chamada externa dentro de transação (case, seção 2): o arquivo sobe antes da gravação
def enviar(storage, regra: Regra, pasta: str, arquivo: Arquivo) -> str:
    conferir(regra, arquivo)
    caminho = novo_caminho(pasta, arquivo.tipo)
    try:
        storage.enviar(regra.bucket, caminho, arquivo.conteudo, arquivo.tipo)
    except ErroSupabase as erro:
        logger.error("Storage recusou o envio para %s: %s", regra.bucket, erro)
        raise ServicoIndisponivel("Não foi possível guardar o arquivo. Tente de novo")
    return caminho


# se a gravação no banco falhar, o arquivo enviado sai do Storage (como o login no cadastro, ADR 0008)
def desfazer_envio(storage, regra: Regra, caminho: str) -> None:
    try:
        storage.apagar(regra.bucket, caminho)
    except ErroSupabase:
        logger.error("arquivo %s/%s ficou sem registro no banco e não pôde ser apagado", regra.bucket, caminho)


# arquivo substituído ou tirado: o registro já não aponta para ele; se o Storage falhar, só fica sobrando
def apagar(storage, regra: Regra, caminho: str) -> None:
    try:
        storage.apagar(regra.bucket, caminho)
    except ErroSupabase:
        logger.error("arquivo %s/%s não é mais usado e não pôde ser apagado", regra.bucket, caminho)


def url_temporaria(storage, regra: Regra, caminho: str) -> str | None:
    try:
        return storage.url_temporaria(regra.bucket, caminho)
    except ErroSupabase as erro:
        # sem o link, a tela mostra o registro sem o arquivo em vez de falhar inteira
        logger.error("não foi possível gerar o link de %s/%s: %s", regra.bucket, caminho, erro)
        return None
