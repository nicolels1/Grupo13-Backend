from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base


# de onde veio cada registro migrado da Vulto e o que a migração mudou nele (P4). Um registro pode ter
# várias linhas: uma por linha de origem (K1 junta até 3 cadastros) e uma por campo alterado, com o
# valor original e a decisão do registro que o mudou. Sem FK: aponta para registros de várias tabelas.
# Gravada só pela carga e nunca alterada (trigger bloqueia_alteracao)
class OrigemMigracao(Base):
    __tablename__ = "origem_migracao"

    id_origem: Mapped[int] = mapped_column(primary_key=True)
    tabela: Mapped[str] = mapped_column(String(40))  # tabela da plataforma (pedido, variante...)
    id_registro: Mapped[str] = mapped_column(String(40))  # id do registro como texto: número ou UUID
    fonte: Mapped[str] = mapped_column(String(100))  # arquivo da Vulto (vulto.db, planilha do catálogo...)
    linha_origem: Mapped[str] = mapped_column(String(60))  # como no staging: vendas#272, Clientes!15
    # vazio: a linha só diz de onde o registro veio; preenchido: o valor original desse campo
    campo: Mapped[str | None] = mapped_column(String(60))
    valor_original: Mapped[str | None] = mapped_column(Text)
    decisao: Mapped[str | None] = mapped_column(String(10))  # número no registro de decisões (K2, V5...)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("tabela ~ '^[a-z_]+$'", name="tabela_valida"),
        CheckConstraint("decisao ~ '^[A-Z][0-9]+[a-z]?$'", name="decisao_valida"),
        CheckConstraint("valor_original IS NULL OR campo IS NOT NULL", name="valor_original_exige_campo"),
        # a origem de um registro (a demo junta com pedido, chamado, cliente...)
        Index("ix_origem_migracao_tabela_id_registro", "tabela", "id_registro"),
    )
