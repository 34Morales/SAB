"""Add regional settings to companies.

Revision ID: 0004_company_regional_settings
Revises: 0003_company_administration
"""

from alembic import op
import sqlalchemy as sa

revision = "0004_company_regional_settings"
down_revision = "0003_company_administration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_columns = {column["name"] for column in sa.inspect(bind).get_columns("companies")}
    for name, column in (
        ("currency_code", sa.Column("currency_code", sa.String(3), nullable=False, server_default="MXN")),
        ("timezone", sa.Column("timezone", sa.String(64), nullable=False, server_default="America/Mexico_City")),
        ("locale", sa.Column("locale", sa.String(16), nullable=False, server_default="es-MX")),
    ):
        if name not in existing_columns:
            op.add_column("companies", column)


def downgrade() -> None:
    bind = op.get_bind()
    existing_columns = {column["name"] for column in sa.inspect(bind).get_columns("companies")}
    for name in ("locale", "timezone", "currency_code"):
        if name in existing_columns:
            op.drop_column("companies", name)
