"""Add permission for managing branches within the active company.

Revision ID: 0003_company_administration
Revises: 0002_identity_security
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_company_administration"
down_revision = "0002_identity_security"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
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

    permission_id = bind.execute(
        sa.select(permissions.c.id).where(permissions.c.code == "companies:manage")
    ).scalar_one_or_none()
    if permission_id is None:
        permission_id = bind.execute(
            permissions.insert().values(
                code="companies:manage",
                description="Create and update branches in the active company",
            )
        ).inserted_primary_key[0]

    admin_id = bind.execute(
        sa.select(roles.c.id).where(roles.c.name == "admin", roles.c.company_id.is_(None))
    ).scalar_one()
    linked = bind.execute(
        sa.select(links.c.role_id).where(
            links.c.role_id == admin_id,
            links.c.permission_id == permission_id,
        )
    ).first()
    if linked is None:
        bind.execute(links.insert().values(role_id=admin_id, permission_id=permission_id))


def downgrade() -> None:
    bind = op.get_bind()
    permission_id = bind.execute(
        sa.text("SELECT id FROM permissions WHERE code = 'companies:manage'")
    ).scalar_one_or_none()
    if permission_id is not None:
        bind.execute(sa.text("DELETE FROM role_permissions WHERE permission_id = :permission_id"), {"permission_id": permission_id})
        bind.execute(sa.text("DELETE FROM permissions WHERE id = :permission_id"), {"permission_id": permission_id})
