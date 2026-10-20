"""Authorization dependency checks for identity and company scope."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.api.dependencies import current_access, require_permission
from backend.app.modules.catalog import service as catalog_service
from backend.app.modules.catalog.models import CatalogCategory, Product
from backend.app.modules.catalog.schemas import CategoryCreate, ProductCreate, ProductUpdate
from backend.app.modules.catalog.service import CatalogCategoryNotFound
from backend.app.modules.companies import service as company_service
from backend.app.modules.companies.branch_schemas import BranchCreate, BranchUpdate
from backend.app.modules.companies.models import Branch, Company
from backend.app.modules.companies.schemas import CompanySettingsUpdate
from backend.app.modules.identity.models import AuthSession, Membership, Role, SecurityEvent, User
from backend.app.modules.identity.security import hash_password, issue_token, token_digest
from backend.app.modules.identity.schemas import RoleCreate, RoleUpdate
from backend.app.modules.identity.service import create_custom_role, delete_custom_role, revoke_token, update_custom_role, update_membership


def test_access_control_rejects_unauthenticated_unpermitted_and_cross_company_access():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with engine.connect() as connection:
        config = Config("backend/alembic.ini")
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    with factory.begin() as db:
        company_a = Company(legal_name="Access Company A", country_code="MX")
        company_b = Company(legal_name="Access Company B", country_code="MX")
        admin = User(email="access-admin@example.test", full_name="Access Admin", password_hash=hash_password("admin-test-password"))
        viewer = User(email="access-viewer@example.test", full_name="Access Viewer", password_hash=hash_password("viewer-test-password"))
        db.add_all([company_a, company_b, admin, viewer])
        db.flush()
        admin_role = db.scalar(select(Role).where(Role.name == "admin", Role.company_id.is_(None)))
        viewer_role = db.scalar(select(Role).where(Role.name == "viewer", Role.company_id.is_(None)))
        admin_membership = Membership(company=company_a, user=admin, roles=[admin_role])
        viewer_membership = Membership(company=company_a, user=viewer, roles=[viewer_role])
        admin_token, viewer_token = issue_token(), issue_token()
        db.add_all([admin_membership, viewer_membership])
        db.flush()
        db.add_all([
            AuthSession(user=admin, token_digest=token_digest(admin_token), expires_at=datetime.now(timezone.utc) + timedelta(hours=1)),
            AuthSession(user=viewer, token_digest=token_digest(viewer_token), expires_at=datetime.now(timezone.utc) + timedelta(hours=1)),
        ])
        db.flush()
        company_a_id, company_b_id = company_a.id, company_b.id
        admin_membership_id = admin_membership.id
        viewer_membership_id = viewer_membership.id

    def credentials(token: str) -> HTTPAuthorizationCredentials:
        return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    try:
        with factory() as db:
            with pytest.raises(HTTPException) as missing:
                current_access(None, company_a_id, db)
            assert missing.value.status_code == 401

            with pytest.raises(HTTPException) as invalid:
                current_access(credentials("invalid-token"), company_a_id, db)
            assert invalid.value.status_code == 403

            admin_context = current_access(credentials(admin_token), company_a_id, db)
            assert require_permission("identity:read")(admin_context) is admin_context
            assert require_permission("companies:manage")(admin_context) is admin_context
            configured = company_service.update_company_settings(
                db,
                company_a_id,
                CompanySettingsUpdate(currency_code="USD", timezone="America/Mexico_City", locale="en-US"),
                admin_context.user.id,
                "192.0.2.10",
            )
            assert configured is not None and configured.currency_code == "USD" and configured.locale == "en-US"
            assert company_service.get_company(db, company_b_id).currency_code == "MXN"
            with pytest.raises(ValidationError):
                CompanySettingsUpdate(timezone="Mars/Olympus")
            with pytest.raises(HTTPException) as cross_company:
                current_access(credentials(admin_token), company_b_id, db)
            assert cross_company.value.status_code == 403

            viewer_context = current_access(credentials(viewer_token), company_a_id, db)
            with pytest.raises(HTTPException) as insufficient:
                require_permission("identity:read")(viewer_context)
            assert insufficient.value.status_code == 403
            with pytest.raises(HTTPException) as cannot_manage_branches:
                require_permission("companies:manage")(viewer_context)
            assert cannot_manage_branches.value.status_code == 403
            assert require_permission("catalog:read")(viewer_context) is viewer_context
            assert require_permission("inventory:read")(viewer_context) is viewer_context
            assert require_permission("purchasing:read")(viewer_context) is viewer_context
            assert require_permission("sales:read")(viewer_context) is viewer_context
            with pytest.raises(HTTPException) as cannot_manage_catalog:
                require_permission("catalog:manage")(viewer_context)
            assert cannot_manage_catalog.value.status_code == 403
            with pytest.raises(HTTPException) as cannot_manage_inventory:
                require_permission("inventory:manage")(viewer_context)
            assert cannot_manage_inventory.value.status_code == 403
            with pytest.raises(HTTPException) as cannot_manage_purchasing:
                require_permission("purchasing:manage")(viewer_context)
            assert cannot_manage_purchasing.value.status_code == 403
            with pytest.raises(HTTPException) as cannot_manage_sales:
                require_permission("sales:manage")(viewer_context)
            assert cannot_manage_sales.value.status_code == 403

            category = catalog_service.create_category(
                db, company_a_id, CategoryCreate(name=" Office Supplies "), admin_context.user.id, "192.0.2.10"
            )
            product = catalog_service.create_product(
                db, company_a_id,
                ProductCreate(
                    sku="paper-001", name="Paper", kind="goods", unit_code="EA",
                    price=Decimal("10.25"), tax_rate=Decimal("16.00"), category_id=category.id,
                ),
                admin_context.user.id,
                "192.0.2.10",
            )
            assert product.sku == "PAPER-001"
            assert [item.id for item in catalog_service.list_products(db, company_a_id, 20, 0)] == [product.id]
            assert catalog_service.list_products(db, company_b_id, 20, 0) == []
            with pytest.raises(ValidationError):
                ProductCreate(sku="BAD-PRICE", name="Invalid", kind="goods", unit_code="EA", price=Decimal("-0.01"))
            with pytest.raises(IntegrityError):
                catalog_service.create_product(
                    db, company_a_id,
                    ProductCreate(sku="paper-001", name="Duplicate SKU", kind="goods", unit_code="EA", price=Decimal("1")),
                    admin_context.user.id,
                    "192.0.2.10",
                )
            db.rollback()
            foreign_category = CatalogCategory(company_id=company_b_id, name="Private")
            db.add(foreign_category)
            db.commit()
            with pytest.raises(CatalogCategoryNotFound):
                catalog_service.create_product(
                    db, company_a_id,
                    ProductCreate(sku="X-1", name="Cross tenant", kind="service", unit_code="EA", price=Decimal("1"), category_id=foreign_category.id),
                    admin_context.user.id,
                    "192.0.2.10",
                )
            foreign_product = Product(
                company_id=company_b_id,
                category_id=foreign_category.id,
                sku="FOREIGN-1",
                name="Private product",
                kind="goods",
                unit_code="EA",
                price=Decimal("2.00"),
                tax_rate=Decimal("0"),
            )
            db.add(foreign_product)
            db.commit()
            assert catalog_service.update_product(
                db, company_a_id, foreign_product.id, ProductUpdate(name="Wrong tenant"), admin_context.user.id, "192.0.2.10"
            ) is None

            branch = company_service.create_branch(
                db, company_a_id, BranchCreate(code="north-1", name="North Branch"), admin_context.user.id, "192.0.2.10"
            )
            assert [item.id for item in company_service.list_branches(db, company_a_id, 50, 0)] == [branch.id]
            assert company_service.list_branches(db, company_b_id, 50, 0) == []
            foreign_branch = Branch(company_id=company_b_id, code="EAST-1", name="East Branch")
            db.add(foreign_branch)
            db.commit()
            assert company_service.update_branch(
                db, company_a_id, foreign_branch.id, BranchUpdate(name="Changed"), admin_context.user.id, "192.0.2.10"
            ) is None
            with pytest.raises(IntegrityError):
                company_service.create_branch(
                    db, company_a_id, BranchCreate(code="NORTH-1", name="Duplicate"), admin_context.user.id, "192.0.2.10"
                )
            db.rollback()
            updated_branch = company_service.update_branch(
                db, company_a_id, branch.id, BranchUpdate(is_active=False), admin_context.user.id, "192.0.2.10"
            )
            assert updated_branch is not None and not updated_branch.is_active
            assert db.scalar(select(SecurityEvent).where(
                SecurityEvent.company_id == company_a_id, SecurityEvent.event_type == "branch_deactivated"
            )) is not None
            assert db.scalar(select(SecurityEvent).where(
                SecurityEvent.company_id == company_a_id, SecurityEvent.event_type == "company_settings_updated"
            )) is not None
            assert db.scalar(select(SecurityEvent).where(
                SecurityEvent.company_id == company_a_id, SecurityEvent.event_type == "product_created"
            )) is not None

            operator_role = db.scalar(select(Role).where(Role.name == "operator", Role.company_id.is_(None)))
            changed_membership = update_membership(
                db, company_a_id, viewer_membership_id, {"role_ids": [operator_role.id]}, admin_context.user.id, "192.0.2.10"
            )
            assert changed_membership is not None and [role.id for role in changed_membership.roles] == [operator_role.id]
            custom_role = create_custom_role(
                db, company_a_id, RoleCreate(name="inventory-reader", permissions=["inventory:read"]), admin_context.user.id, "192.0.2.10"
            )
            custom_role = update_custom_role(
                db, company_a_id, custom_role.id, RoleUpdate(name="stock-reader", permissions=["inventory:read", "catalog:read"]), admin_context.user.id, "192.0.2.10"
            )
            assert custom_role is not None and custom_role.name == "stock-reader"
            changed_membership = update_membership(
                db, company_a_id, viewer_membership_id, {"role_ids": [custom_role.id]}, admin_context.user.id, None
            )
            assert changed_membership is not None and changed_membership.roles[0].id == custom_role.id
            with pytest.raises(ValueError, match="Reassign all memberships"):
                delete_custom_role(db, company_a_id, custom_role.id, admin_context.user.id, None)
            db.rollback()
            delegated_admin = create_custom_role(
                db, company_a_id, RoleCreate(name="delegated-admin", permissions=["identity:manage"]), admin_context.user.id, None
            )
            update_membership(db, company_a_id, viewer_membership_id, {"role_ids": [delegated_admin.id]}, admin_context.user.id, None)
            with pytest.raises(ValueError, match="Reassign active members"):
                update_custom_role(db, company_a_id, delegated_admin.id, RoleUpdate(permissions=["catalog:read"]), admin_context.user.id, None)
            db.rollback()
            update_membership(db, company_a_id, viewer_membership_id, {"role_ids": [custom_role.id]}, admin_context.user.id, None)
            assert update_custom_role(db, company_a_id, operator_role.id, RoleUpdate(name="tampered"), admin_context.user.id, None) is None
            foreign_role = Role(company_id=company_b_id, name="private-role")
            db.add(foreign_role)
            db.commit()
            with pytest.raises(LookupError, match="not available"):
                update_membership(db, company_a_id, viewer_membership_id, {"role_ids": [foreign_role.id]}, admin_context.user.id, None)
            db.rollback()

            changed_membership = update_membership(
                db, company_a_id, viewer_membership_id, {"is_active": False}, admin_context.user.id, "192.0.2.10"
            )
            assert changed_membership is not None and changed_membership.is_active is False
            with pytest.raises(ValueError, match="at least one active identity administrator"):
                update_membership(db, company_a_id, admin_membership_id, {"is_active": False}, admin_context.user.id, None)
            db.rollback()
            with pytest.raises(HTTPException) as inactive_membership:
                current_access(credentials(viewer_token), company_a_id, db)
            assert inactive_membership.value.status_code == 403

            db.get(Membership, admin_membership_id).is_active = False
            db.commit()
            with pytest.raises(HTTPException) as revoked_membership:
                current_access(credentials(admin_token), company_a_id, db)
            assert revoked_membership.value.status_code == 403

            revoke_token(db, viewer_token)
            with pytest.raises(HTTPException) as revoked_session:
                current_access(credentials(viewer_token), company_a_id, db)
            assert revoked_session.value.status_code == 403

            unused_role = create_custom_role(
                db, company_a_id, RoleCreate(name="unused-role", permissions=["catalog:read"]), admin_context.user.id, None
            )
            assert delete_custom_role(db, company_a_id, unused_role.id, admin_context.user.id, None) is True
            assert db.get(Role, unused_role.id) is None
            assert delete_custom_role(db, company_a_id, operator_role.id, admin_context.user.id, None) is None
            assert db.scalar(select(SecurityEvent).where(
                SecurityEvent.company_id == company_a_id, SecurityEvent.event_type == "identity_role_deleted",
                SecurityEvent.details["role_id"].as_integer() == unused_role.id,
            )) is not None
    finally:
        engine.dispose()
