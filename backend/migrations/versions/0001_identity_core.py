"""Create company and identity schema and seed baseline RBAC catalog.

Revision ID: 0001_identity_core
Revises:
"""

from alembic import op
import sqlalchemy as sa

revision = "0001_identity_core"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep this revision frozen at the original baseline. Adopt existing prototype tables safely.
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "companies" not in existing:
        op.create_table(
            "companies",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("legal_name", sa.String(200), nullable=False),
            sa.Column("tax_id", sa.String(20)),
            sa.Column("country_code", sa.String(2), server_default="MX", nullable=False),
            sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "branches" not in existing:
        op.create_table(
            "branches",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
            sa.Column("code", sa.String(20), nullable=False),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("company_id", "code", name="uq_branches_company_code"),
        )
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "users" not in existing:
        op.create_table(
            "users",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("email", sa.String(254), nullable=False, unique=True),
            sa.Column("full_name", sa.String(160), nullable=False),
            sa.Column("password_hash", sa.String(256), nullable=False),
            sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "memberships" not in existing:
        op.create_table(
            "memberships",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
            sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("user_id", "company_id", name="uq_memberships_user_company"),
        )
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "roles" not in existing:
        op.create_table(
            "roles",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE")),
            sa.Column("name", sa.String(80), nullable=False),
            sa.Column("is_system", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("company_id", "name", name="uq_roles_company_name"),
        )
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "permissions" not in existing:
        op.create_table(
            "permissions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("code", sa.String(100), nullable=False, unique=True),
            sa.Column("description", sa.String(240), nullable=False),
        )
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "role_permissions" not in existing:
        op.create_table(
            "role_permissions",
            sa.Column("role_id", sa.Integer(), sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("permission_id", sa.Integer(), sa.ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
        )
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "membership_roles" not in existing:
        op.create_table(
            "membership_roles",
            sa.Column("membership_id", sa.Integer(), sa.ForeignKey("memberships.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("role_id", sa.Integer(), sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
        )
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "auth_sessions" not in existing:
        op.create_table(
            "auth_sessions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("token_digest", sa.String(64), nullable=False, unique=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    indexes = {index["name"] for table in ("companies", "branches", "users", "memberships", "auth_sessions") for index in sa.inspect(op.get_bind()).get_indexes(table)}
    for name, table, columns in (
        ("ix_companies_active", "companies", ["is_active"]),
        ("ix_branches_company_active", "branches", ["company_id", "is_active"]),
        ("ix_users_active_email", "users", ["email", "is_active"]),
        ("ix_memberships_company_active", "memberships", ["company_id", "is_active"]),
        ("ix_auth_sessions_user_expiry", "auth_sessions", ["user_id", "expires_at"]),
    ):
        if name not in indexes:
            op.create_index(name, table, columns)
    seed_metadata = sa.MetaData()
    permissions = sa.Table("permissions", seed_metadata, sa.Column("id", sa.Integer, primary_key=True), sa.Column("code", sa.String), sa.Column("description", sa.String))
    roles = sa.Table("roles", seed_metadata, sa.Column("id", sa.Integer, primary_key=True), sa.Column("company_id", sa.Integer), sa.Column("name", sa.String), sa.Column("is_system", sa.Boolean))
    role_permissions = sa.table("role_permissions", sa.column("role_id", sa.Integer), sa.column("permission_id", sa.Integer))
    permission_rows = [
        {"id": 1, "code": "companies:read", "description": "View companies where the user has membership"},
        {"id": 2, "code": "companies:create", "description": "Create a company and become its administrator"},
        {"id": 3, "code": "identity:read", "description": "View users and roles in the active company"},
        {"id": 4, "code": "identity:manage", "description": "Create users and assign roles in the active company"},
    ]
    bind = op.get_bind()
    permission_ids = {}
    for row in permission_rows:
        result = bind.execute(permissions.insert().values(code=row["code"], description=row["description"]))
        permission_ids[row["code"]] = result.inserted_primary_key[0]
    role_ids = {}
    for name in ("admin", "operator", "viewer"):
        result = bind.execute(roles.insert().values(company_id=None, name=name, is_system=True))
        role_ids[name] = result.inserted_primary_key[0]
    links = [
        {"role_id": role_ids["admin"], "permission_id": permission_id}
        for permission_id in permission_ids.values()
    ] + [
        {"role_id": role_ids[name], "permission_id": permission_ids["companies:read"]}
        for name in ("operator", "viewer")
    ]
    op.bulk_insert(role_permissions, links)


def downgrade() -> None:
    bind = op.get_bind()
    for table in ("auth_sessions", "membership_roles", "role_permissions", "permissions", "roles", "memberships", "users", "branches", "companies"):
        if sa.inspect(bind).has_table(table):
            op.drop_table(table)
