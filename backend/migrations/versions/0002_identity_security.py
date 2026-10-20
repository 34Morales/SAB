"""Add MFA, recovery, login throttling and append-only security events.

Revision ID: 0002_identity_security
Revises: 0001_identity_core
"""

from alembic import op
import sqlalchemy as sa

from backend.app.database.base import Base
from backend.app.modules.companies import models as _companies  # noqa: F401
from backend.app.modules.identity import models as _identity  # noqa: F401

revision = "0002_identity_security"
down_revision = "0001_identity_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_columns = {column["name"] for column in sa.inspect(bind).get_columns("users")}
    for name, column in (
        ("mfa_enabled", sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default=sa.false())),
        ("mfa_secret_encrypted", sa.Column("mfa_secret_encrypted", sa.String(512), nullable=True)),
        ("mfa_pending_secret_encrypted", sa.Column("mfa_pending_secret_encrypted", sa.String(512), nullable=True)),
        ("mfa_last_used_step", sa.Column("mfa_last_used_step", sa.Integer(), nullable=True)),
    ):
        if name not in existing_columns:
            op.add_column("users", column)
    Base.metadata.create_all(bind=bind, checkfirst=True)

    seed_metadata = sa.MetaData()
    permissions = sa.Table("permissions", seed_metadata, sa.Column("id", sa.Integer, primary_key=True), sa.Column("code", sa.String), sa.Column("description", sa.String))
    role_permissions = sa.table("role_permissions", sa.column("role_id", sa.Integer), sa.column("permission_id", sa.Integer))
    result = bind.execute(permissions.insert().values(
        code="security:audit:read", description="View security events for the active company"
    ))
    roles_table = sa.Table("roles", seed_metadata, sa.Column("id", sa.Integer, primary_key=True), sa.Column("name", sa.String))
    admin_id = bind.execute(sa.select(roles_table.c.id).where(roles_table.c.name == "admin")).scalar_one()
    op.bulk_insert(role_permissions, [{"role_id": admin_id, "permission_id": result.inserted_primary_key[0]}])

    if bind.dialect.name == "sqlite":
        op.execute("CREATE TRIGGER security_events_no_update BEFORE UPDATE ON security_events BEGIN SELECT RAISE(ABORT, 'security_events is append-only'); END")
        op.execute("CREATE TRIGGER security_events_no_delete BEFORE DELETE ON security_events BEGIN SELECT RAISE(ABORT, 'security_events is append-only'); END")
    elif bind.dialect.name == "postgresql":
        op.execute("CREATE FUNCTION reject_security_event_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'security_events is append-only'; END; $$")
        op.execute("CREATE TRIGGER security_events_no_update BEFORE UPDATE OR DELETE ON security_events FOR EACH ROW EXECUTE FUNCTION reject_security_event_mutation()")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS security_events_no_update")
        op.execute("DROP TRIGGER IF EXISTS security_events_no_delete")
    elif bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS security_events_no_update ON security_events")
        op.execute("DROP FUNCTION IF EXISTS reject_security_event_mutation()")
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code = 'security:audit:read')")
    op.execute("DELETE FROM permissions WHERE code = 'security:audit:read'")
    for table in ("security_events", "recovery_email_outbox", "password_recovery_tokens", "mfa_recovery_codes", "login_throttles"):
        if sa.inspect(bind).has_table(table):
            op.drop_table(table)
    existing_columns = {column["name"] for column in sa.inspect(bind).get_columns("users")}
    for name in ("mfa_last_used_step", "mfa_pending_secret_encrypted", "mfa_secret_encrypted", "mfa_enabled"):
        if name in existing_columns:
            op.drop_column("users", name)
