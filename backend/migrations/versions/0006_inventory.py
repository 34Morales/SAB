"""Add traceable company inventory and permissions.

Revision ID: 0006_inventory
Revises: 0005_catalog
"""

from alembic import op
import sqlalchemy as sa

from backend.app.database.base import Base
from backend.app.modules.inventory import models as _inventory_models  # noqa: F401

revision = "0006_inventory"
down_revision = "0005_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "allows_fractional_quantity" not in {c["name"] for c in sa.inspect(bind).get_columns("products")}:
        op.add_column("products", sa.Column("allows_fractional_quantity", sa.Boolean(), nullable=False, server_default=sa.false()))
    if "allow_negative_stock" not in {c["name"] for c in sa.inspect(bind).get_columns("companies")}:
        op.add_column("companies", sa.Column("allow_negative_stock", sa.Boolean(), nullable=False, server_default=sa.false()))
    Base.metadata.create_all(bind=bind, checkfirst=True)

    metadata = sa.MetaData()
    permissions = sa.Table("permissions", metadata, sa.Column("id", sa.Integer, primary_key=True), sa.Column("code", sa.String), sa.Column("description", sa.String))
    roles = sa.Table("roles", metadata, sa.Column("id", sa.Integer, primary_key=True), sa.Column("company_id", sa.Integer), sa.Column("name", sa.String))
    links = sa.Table("role_permissions", metadata, sa.Column("role_id", sa.Integer, primary_key=True), sa.Column("permission_id", sa.Integer, primary_key=True))
    for code, description in (("inventory:read", "View inventory balances and movement history"), ("inventory:manage", "Manage warehouses and post inventory operations")):
        pid = bind.execute(sa.select(permissions.c.id).where(permissions.c.code == code)).scalar_one_or_none()
        if pid is None:
            pid = bind.execute(permissions.insert().values(code=code, description=description)).inserted_primary_key[0]
        role_names = ("admin", "operator") if code == "inventory:manage" else ("admin", "operator", "viewer")
        for rid in bind.execute(sa.select(roles.c.id).where(roles.c.name.in_(role_names), roles.c.company_id.is_(None))).scalars():
            if bind.execute(sa.select(links.c.role_id).where(links.c.role_id == rid, links.c.permission_id == pid)).first() is None:
                bind.execute(links.insert().values(role_id=rid, permission_id=pid))

    if bind.dialect.name == "sqlite":
        op.execute("CREATE TRIGGER IF NOT EXISTS trg_inventory_operations_no_update BEFORE UPDATE ON inventory_operations BEGIN SELECT RAISE(ABORT, 'inventory operations are immutable'); END")
        op.execute("CREATE TRIGGER IF NOT EXISTS trg_inventory_operations_no_delete BEFORE DELETE ON inventory_operations BEGIN SELECT RAISE(ABORT, 'inventory operations are immutable'); END")
        op.execute("CREATE TRIGGER IF NOT EXISTS trg_inventory_movements_no_update BEFORE UPDATE ON inventory_movements BEGIN SELECT RAISE(ABORT, 'inventory movements are immutable'); END")
        op.execute("CREATE TRIGGER IF NOT EXISTS trg_inventory_movements_no_delete BEFORE DELETE ON inventory_movements BEGIN SELECT RAISE(ABORT, 'inventory movements are immutable'); END")
    elif bind.dialect.name == "postgresql":
        op.execute("CREATE FUNCTION reject_inventory_operation_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'inventory operations are immutable'; END; $$")
        op.execute("CREATE TRIGGER trg_inventory_operations_immutable BEFORE UPDATE OR DELETE ON inventory_operations FOR EACH ROW EXECUTE FUNCTION reject_inventory_operation_mutation()")
        op.execute("CREATE FUNCTION reject_inventory_movement_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'inventory movements are immutable'; END; $$")
        op.execute("CREATE TRIGGER trg_inventory_movements_immutable BEFORE UPDATE OR DELETE ON inventory_movements FOR EACH ROW EXECUTE FUNCTION reject_inventory_movement_mutation()")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_inventory_operations_no_update")
        op.execute("DROP TRIGGER IF EXISTS trg_inventory_operations_no_delete")
        op.execute("DROP TRIGGER IF EXISTS trg_inventory_movements_no_update")
        op.execute("DROP TRIGGER IF EXISTS trg_inventory_movements_no_delete")
    elif bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS trg_inventory_movements_immutable ON inventory_movements")
        op.execute("DROP TRIGGER IF EXISTS trg_inventory_operations_immutable ON inventory_operations")
        op.execute("DROP FUNCTION IF EXISTS reject_inventory_movement_mutation()")
        op.execute("DROP FUNCTION IF EXISTS reject_inventory_operation_mutation()")
    bind.execute(sa.text("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ('inventory:read','inventory:manage'))"))
    bind.execute(sa.text("DELETE FROM permissions WHERE code IN ('inventory:read','inventory:manage')"))
    for table in ("inventory_movements", "inventory_balances", "inventory_operations", "inventory_locations", "warehouses"):
        if sa.inspect(bind).has_table(table):
            op.drop_table(table)
    for table, name in (("products", "allows_fractional_quantity"), ("companies", "allow_negative_stock")):
        if name in {c["name"] for c in sa.inspect(bind).get_columns(table)}:
            op.drop_column(table, name)
