"""Add suppliers, purchase documents, receipts, returns and payables.

Revision ID: 0007_purchasing
Revises: 0006_inventory
"""

from alembic import op
import sqlalchemy as sa

from backend.app.database.base import Base
from backend.app.modules.purchasing import models as _purchasing_models  # noqa: F401

revision = "0007_purchasing"
down_revision = "0006_inventory"
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
        ("purchasing:read", "View suppliers, purchase orders, receipts, returns and supplier invoices"),
        ("purchasing:manage", "Manage suppliers, purchasing documents, receipts, returns and payments"),
    ):
        permission_id = bind.execute(sa.select(permissions.c.id).where(permissions.c.code == code)).scalar_one_or_none()
        if permission_id is None:
            permission_id = bind.execute(permissions.insert().values(code=code, description=description)).inserted_primary_key[0]
        role_names = ("admin", "operator") if code == "purchasing:manage" else ("admin", "operator", "viewer")
        role_ids = bind.execute(sa.select(roles.c.id).where(roles.c.name.in_(role_names), roles.c.company_id.is_(None))).scalars().all()
        for role_id in role_ids:
            if bind.execute(sa.select(links.c.role_id).where(links.c.role_id == role_id, links.c.permission_id == permission_id)).first() is None:
                bind.execute(links.insert().values(role_id=role_id, permission_id=permission_id))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(
        "DELETE FROM role_permissions WHERE permission_id IN "
        "(SELECT id FROM permissions WHERE code IN ('purchasing:read','purchasing:manage'))"
    ))
    bind.execute(sa.text("DELETE FROM permissions WHERE code IN ('purchasing:read','purchasing:manage')"))
    for table in (
        "supplier_payments", "supplier_invoice_lines", "supplier_invoices",
        "supplier_return_lines", "supplier_returns", "purchase_receipt_lines",
        "purchase_receipts", "purchase_order_events", "purchase_order_lines",
        "purchase_orders", "suppliers",
    ):
        if sa.inspect(bind).has_table(table):
            op.drop_table(table)
