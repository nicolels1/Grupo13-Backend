from dataclasses import dataclass

from fastapi import UploadFile

# lê no máximo o maior limite aceito (anexo de chamado, 10 MB) + 1 byte: basta para recusar o excesso
LEITURA_MAXIMA = 10 * 1024 * 1024 + 1


@dataclass
class Arquivo:
    nome: str
    tipo: str
    conteudo: bytes


def ler_upload(arquivo: UploadFile) -> Arquivo:
    conteudo = arquivo.file.read(LEITURA_MAXIMA)
    return Arquivo(nome=arquivo.filename or "arquivo", tipo=arquivo.content_type or "", conteudo=conteudo)
