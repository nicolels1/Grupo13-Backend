import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base

AVALIACOES = ("positiva", "negativa")
PAPEIS = ("usuario", "assistente", "ferramenta")
# o que a ferramenta trouxe: dado, nada (busca vazia), erro de verdade ou recusa por permissão
RESULTADOS = ("dado", "vazio", "falhou", "sem_permissao", "invalido")


# conversa de uma pessoa da equipe com o assistente interno. Termina depois de 30 min parada: a próxima
# pergunta começa outra, sem memória
class ConversaInterna(Base):
    __tablename__ = "conversa_interna"

    id_conversa: Mapped[int] = mapped_column(primary_key=True)
    id_usuario: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("usuario.id_usuario", ondelete="RESTRICT"))
    iniciada_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ultima_msg_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    avaliacao: Mapped[str | None] = mapped_column(String(10))  # 👍/👎 opcional

    __table_args__ = (
        CheckConstraint(f"avaliacao IN {AVALIACOES}", name="avaliacao_valida"),
        Index("ix_conversa_interna_usuario", "id_usuario", "ultima_msg_em"),
    )


# cada mensagem da conversa: a pergunta, a resposta (com modelo, tokens e tempo, para o custo real) e cada
# chamada de ferramenta (com os parâmetros, para a auditoria de onde veio cada número). O texto pode trazer
# dado de cliente: a rotina diária apaga o texto com mais de 90 dias e mantém o resto
class MensagemInterna(Base):
    __tablename__ = "mensagem_interna"

    id_mensagem: Mapped[int] = mapped_column(primary_key=True)
    id_conversa: Mapped[int] = mapped_column(ForeignKey("conversa_interna.id_conversa", ondelete="CASCADE"))
    papel: Mapped[str] = mapped_column(String(10))
    texto: Mapped[str | None] = mapped_column(Text)
    ferramenta: Mapped[str | None] = mapped_column(String(40))
    parametros: Mapped[dict | None] = mapped_column(JSONB)
    resultado: Mapped[str | None] = mapped_column(String(15))
    # a resposta passou na conferência dos números; falso = mostrou o texto fixo da ferramenta
    numeros_conferidos: Mapped[bool | None] = mapped_column()
    modelo: Mapped[str | None] = mapped_column(String(40))
    tokens_entrada: Mapped[int | None] = mapped_column()
    tokens_saida: Mapped[int | None] = mapped_column()
    tokens_cache_lidos: Mapped[int | None] = mapped_column()
    tokens_cache_escritos: Mapped[int | None] = mapped_column()
    duracao_ms: Mapped[int | None] = mapped_column()
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(f"papel IN {PAPEIS}", name="papel_valido"),
        CheckConstraint(f"resultado IN {RESULTADOS}", name="resultado_valido"),
        CheckConstraint("papel = 'ferramenta' OR (ferramenta IS NULL AND resultado IS NULL)", name="so_ferramenta"),
        Index("ix_mensagem_interna_conversa", "id_conversa", "criado_em"),
    )
