"""Create company-scoped product catalog.

Revision ID: 0005_catalog
Revises: 0004_company_regional_settings
"""

from alembic import op
import sqlalchemy as sa

from backend.app.database.base import Base
from backend.app.modules.catalog import models as _catalog_models  # noqa: F401

revision = "0005_catalog"
down_revision = "0004_company_regional_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, checkfirst=True)
    metadata = sa.MetaData()
    permissions = sa.Table(
        "permissions", metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.String),
        sa.Column("description", sa.String),
    )
    roles = sa.Table(
        "roles", metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("company_id", sa.Integer),
        sa.Column("name", sa.String),
    )
    links = sa.Table(
        "role_permissions", metadata,
        sa.Column("role_id", sa.Integer, primary_key=True),
        sa.Column("permission_id", sa.Integer, primary_key=True),
    )
    admin_id = bind.execute(
        sa.select(roles.c.id).where(roles.c.name == "admin", roles.c.company_id.is_(None))
    ).scalar_one()
    for code, description in (
        ("catalog:read", "View catalog entries for the active company"),
        ("catalog:manage", "Create and update catalog entries for the active company"),
    ):
        permission_id = bind.execute(
            sa.select(permissions.c.id).where(permissions.c.code == code)
        ).scalar_one_or_none()
        if permission_id is None:
            permission_id = bind.execute(permissions.insert().values(code=code, description=description)).inserted_primary_key[0]
        if bind.execute(sa.select(links.c.role_id).where(
            links.c.role_id == admin_id, links.c.permission_id == permission_id,
        )).first() is None:
            bind.execute(links.insert().values(role_id=admin_id, permission_id=permission_id))
        if code == "catalog:read":
            read_role_ids = bind.execute(
                sa.select(roles.c.id).where(
                    roles.c.name.in_(("operator", "viewer")), roles.c.company_id.is_(None),
                )
            ).scalars().all()
            for role_id in read_role_ids:
                if bind.execute(sa.select(links.c.role_id).where(
                    links.c.role_id == role_id, links.c.permission_id == permission_id,
                )).first() is None:
                    bind.execute(links.insert().values(role_id=role_id, permission_id=permission_id))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(
        "DELETE FROM role_permissions WHERE permission_id IN "
        "(SELECT id FROM permissions WHERE code IN ('catalog:read', 'catalog:manage'))"
    ))
    bind.execute(sa.text("DELETE FROM permissions WHERE code IN ('catalog:read', 'catalog:manage')"))
    for table in ("products", "catalog_categories"):
        if sa.inspect(bind).has_table(table):
            op.drop_table(table)
