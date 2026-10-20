"""adiciona codigo de barras a variante

Revision ID: 12c45282fc43
Revises: 46fe03abf2fa
Create Date: 2026-10-20 14:41:25.520494

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '12c45282fc43'
down_revision: Union[str, Sequence[str], None] = '46fe03abf2fa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PAPEL = "api_casalorenzi"

# Código de barras (EAN-13) em toda variante (C12, registro de decisões da migração da Vulto). As peças
# da Vulto trazem o de fábrica; as que não têm (as da Lorenzi e as 9 da Vulto cujo EAN o Excel cortou)
# ganham um código interno: 20 + número da variante + dígito verificador. O prefixo 20 é da faixa que a
# GS1 reserva para uso interno da loja e nunca coincide com um EAN de fábrica (os da Vulto começam com 789).
# A mesma conta está em src/utils/ean.py
FUNCOES = {
    "digito_verificador_ean(text)": """
        CREATE FUNCTION digito_verificador_ean(base text) RETURNS text
        LANGUAGE sql IMMUTABLE SET search_path = public AS $$
            -- pesos 1 e 3 alternados nos 12 primeiros dígitos; completa até a dezena
            SELECT ((10 - sum(substr(base, i, 1)::int * CASE WHEN i % 2 = 0 THEN 3 ELSE 1 END) % 10) % 10)::text
            FROM generate_series(1, 12) AS i
        $$;
    """,
    "ean_valido(text)": """
        CREATE FUNCTION ean_valido(ean text) RETURNS boolean
        LANGUAGE sql IMMUTABLE SET search_path = public AS $$
            SELECT CASE WHEN ean ~ '^[0-9]{13}$' THEN right(ean, 1) = digito_verificador_ean(left(ean, 12))
                        ELSE false END
        $$;
    """,
    "ean_interno(integer)": """
        CREATE FUNCTION ean_interno(id_variante integer) RETURNS text
        LANGUAGE sql IMMUTABLE SET search_path = public AS $$
            SELECT base || digito_verificador_ean(base) FROM (SELECT '20' || lpad(id_variante::text, 10, '0') AS base) b
        $$;
    """,
}


def upgrade() -> None:
    """Upgrade schema."""
    # a regra do banco (CHECK) e o trigger chamam as funções com o usuário da API
    for assinatura, criar in FUNCOES.items():
        op.execute(criar)
        op.execute(f"REVOKE ALL ON FUNCTION {assinatura} FROM PUBLIC, anon, authenticated;")
        op.execute(f"GRANT EXECUTE ON FUNCTION {assinatura} TO {PAPEL};")

    # as peças que já existem ganham o código interno antes da coluna virar obrigatória
    op.add_column('variante', sa.Column('ean', sa.String(length=13), nullable=True))
    op.execute("UPDATE variante SET ean = ean_interno(id_variante);")
    op.alter_column('variante', 'ean', nullable=False)
    op.create_unique_constraint(op.f('uq_variante_ean'), 'variante', ['ean'])
    op.create_check_constraint(op.f('ck_variante_ean_valido'), 'variante', "ean_valido(ean)")

    # peça cadastrada sem EAN de fábrica: o id já vem da sequência antes do trigger rodar
    op.execute("""
        CREATE FUNCTION gera_ean_interno() RETURNS trigger
        LANGUAGE plpgsql SET search_path = public AS $$
        BEGIN
            IF NEW.ean IS NULL THEN
                NEW.ean := ean_interno(NEW.id_variante);
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    # disparar um trigger não exige EXECUTE: ninguém chama a função diretamente
    op.execute("REVOKE EXECUTE ON FUNCTION gera_ean_interno() FROM PUBLIC, anon, authenticated;")
    op.execute("""
        CREATE TRIGGER trg_variante_gera_ean BEFORE INSERT ON variante
        FOR EACH ROW EXECUTE FUNCTION gera_ean_interno();
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER trg_variante_gera_ean ON variante;")
    op.execute("DROP FUNCTION gera_ean_interno();")
    op.drop_constraint(op.f('ck_variante_ean_valido'), 'variante', type_='check')
    op.drop_constraint(op.f('uq_variante_ean'), 'variante', type_='unique')
    op.drop_column('variante', 'ean')
    for assinatura in reversed(list(FUNCOES)):
        op.execute(f"DROP FUNCTION {assinatura};")
