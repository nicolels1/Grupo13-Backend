"""aceita o telegram no assistente

Revision ID: d9b9e2d71fb4
Revises: 947b974fd79a
Create Date: 2026-10-28 21:33:14.795588

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd9b9e2d71fb4'
down_revision: Union[str, Sequence[str], None] = '947b974fd79a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Assistente do cliente (Entrega 4): a banca testa pelo bot do Telegram enquanto a Meta não libera o WhatsApp.
# O canal telegram entra na conversa, no resumo anônimo e no chamado aberto pelo assistente. Só troca as regras:
# nenhuma coluna nova. O downgrade falha se já houver linha com telegram (é preciso apagar antes)

CONVERSA_ANTES = "canal IN ('whatsapp', 'chat')"
CONVERSA_DEPOIS = "canal IN ('whatsapp', 'telegram', 'chat')"
CHAMADO_ANTES = "canal_externo IN ('whatsapp', 'instagram', 'email', 'chat')"
CHAMADO_DEPOIS = "canal_externo IN ('whatsapp', 'instagram', 'email', 'telegram', 'chat')"


def _trocar_regras(conversa: str, chamado: str) -> None:
    for tabela in ('conversa_canal', 'resumo_conversa'):
        op.drop_constraint(op.f(f'ck_{tabela}_canal_valido'), tabela, type_='check')
        op.create_check_constraint(op.f(f'ck_{tabela}_canal_valido'), tabela, conversa)
    op.drop_constraint(op.f('ck_chamado_canal_externo_valido'), 'chamado', type_='check')
    op.create_check_constraint(op.f('ck_chamado_canal_externo_valido'), 'chamado', chamado)


def upgrade() -> None:
    """Upgrade schema."""
    _trocar_regras(CONVERSA_DEPOIS, CHAMADO_DEPOIS)


def downgrade() -> None:
    """Downgrade schema."""
    _trocar_regras(CONVERSA_ANTES, CHAMADO_ANTES)
