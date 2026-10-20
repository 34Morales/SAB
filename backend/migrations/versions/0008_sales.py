"""Add customer sales, inventory reservations, deliveries and receivables."""

from alembic import op
import sqlalchemy as sa

from backend.app.database.base import Base
from backend.app.modules.sales import models as _sales_models  # noqa: F401

revision = "0008_sales"
down_revision = "0007_purchasing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, checkfirst=True)
    metadata = sa.MetaData()
    permissions = sa.Table("permissions", metadata, sa.Column("id", sa.Integer, primary_key=True), sa.Column("code", sa.String), sa.Column("description", sa.String))
    roles = sa.Table("roles", metadata, sa.Column("id", sa.Integer, primary_key=True), sa.Column("company_id", sa.Integer), sa.Column("name", sa.String))
    links = sa.Table("role_permissions", metadata, sa.Column("role_id", sa.Integer, primary_key=True), sa.Column("permission_id", sa.Integer, primary_key=True))
    for code, description in (
        ("sales:read", "View customers, quotes, orders, deliveries and receivables"),
        ("sales:manage", "Manage customers, quotes, orders, deliveries, returns, invoices and payments"),
    ):
        permission_id = bind.execute(sa.select(permissions.c.id).where(permissions.c.code == code)).scalar_one_or_none()
        if permission_id is None:
            permission_id = bind.execute(permissions.insert().values(code=code, description=description)).inserted_primary_key[0]
        names = ("admin", "operator") if code == "sales:manage" else ("admin", "operator", "viewer")
        role_ids = bind.execute(sa.select(roles.c.id).where(roles.c.name.in_(names), roles.c.company_id.is_(None))).scalars().all()
        for role_id in role_ids:
            exists = bind.execute(sa.select(links.c.role_id).where(links.c.role_id == role_id, links.c.permission_id == permission_id)).first()
            if exists is None:
                bind.execute(links.insert().values(role_id=role_id, permission_id=permission_id))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ('sales:read','sales:manage'))"))
    bind.execute(sa.text("DELETE FROM permissions WHERE code IN ('sales:read','sales:manage')"))
    for table in (
        "sales_payments", "sales_credit_notes", "sales_invoice_lines", "sales_invoices", "sales_returns",
        "sales_delivery_lines", "sales_deliveries", "sales_events", "sales_order_lines", "sales_orders",
        "sales_quote_lines", "sales_quotes", "customers",
    ):
        if sa.inspect(bind).has_table(table):
            op.drop_table(table)
