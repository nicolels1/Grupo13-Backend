"""tira o chat do assistente

Revision ID: e10623693e07
Revises: d9b9e2d71fb4
Create Date: 2026-10-28 23:00:32.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e10623693e07'
down_revision: Union[str, Sequence[str], None] = 'd9b9e2d71fb4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Assistente do cliente (Entrega 4): a banca testa só pelo bot do Telegram, sem o chat do n8n (Chat Trigger).
# O canal chat sai da conversa, do resumo anônimo e do chamado aberto pelo assistente. Só troca as regras:
# o upgrade falha se já houver linha com chat (é preciso apagar antes)

CONVERSA_ANTES = "canal IN ('whatsapp', 'telegram', 'chat')"
CONVERSA_DEPOIS = "canal IN ('whatsapp', 'telegram')"
CHAMADO_ANTES = "canal_externo IN ('whatsapp', 'instagram', 'email', 'telegram', 'chat')"
CHAMADO_DEPOIS = "canal_externo IN ('whatsapp', 'instagram', 'email', 'telegram')"


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
