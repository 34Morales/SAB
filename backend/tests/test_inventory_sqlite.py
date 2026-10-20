"""SQLite inventory business rules and migration smoke coverage."""

from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.modules.catalog.models import Product
from backend.app.modules.companies.models import Company
from backend.app.modules.identity.models import Role, SecurityEvent, User
from backend.app.modules.identity.security import hash_password
from backend.app.modules.inventory.models import InventoryBalance, InventoryMovement, InventoryOperation, Warehouse
from backend.app.modules.inventory.schemas import InventoryOperationRequest, WarehouseCreate
from backend.app.modules.inventory.service import InventoryError, create_warehouse, deactivate_warehouse, post_operation, reconcile_balances


@pytest.fixture
def inventory_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with engine.connect() as connection:
        config = Config("backend/alembic.ini")
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as db:
        company = Company(legal_name="Inventory test", country_code="MX")
        other_company = Company(legal_name="Other inventory", country_code="MX")
        user = User(email="inventory@example.test", full_name="Inventory Admin", password_hash=hash_password("inventory-test-password"))
        db.add_all([company, other_company, user])
        db.flush()
        product = Product(company_id=company.id, sku="STOCK-1", name="Stock", kind="goods", unit_code="EA", price=Decimal("1"), tax_rate=Decimal("0"))
        foreign_product = Product(company_id=other_company.id, sku="OTHER-1", name="Other", kind="goods", unit_code="EA", price=Decimal("1"), tax_rate=Decimal("0"))
        db.add_all([product, foreign_product])
        db.flush()
        ids = (company.id, other_company.id, user.id, product.id, foreign_product.id)
    try:
        with factory() as db:
            yield db, ids
    finally:
        engine.dispose()


def _op(kind, product_id, warehouse_id, quantity, **extra):
    return InventoryOperationRequest(kind=kind, product_id=product_id, warehouse_id=warehouse_id, quantity=Decimal(quantity), reason=extra.pop("reason", "counted receipt"), **extra)


def test_inventory_operations_are_atomic_scoped_and_reconcilable(inventory_db):
    db, (company_id, other_company_id, user_id, product_id, foreign_product_id) = inventory_db
    warehouse_a = create_warehouse(db, company_id, WarehouseCreate(code="main", name="Main"), user_id, "192.0.2.5")
    warehouse_b = create_warehouse(db, company_id, WarehouseCreate(code="secondary", name="Secondary"), user_id, "192.0.2.5")
    foreign = create_warehouse(db, other_company_id, WarehouseCreate(code="private", name="Private"), user_id, None)

    receipt = post_operation(db, company_id, _op("receipt", product_id, warehouse_a.id, "20"), "receipt-001", user_id, None)
    assert len(db.scalars(select(InventoryMovement).where(InventoryMovement.operation_id == receipt.id)).all()) == 1
    retry = post_operation(db, company_id, _op("receipt", product_id, warehouse_a.id, "20"), "receipt-001", user_id, None)
    assert retry.id == receipt.id
    with pytest.raises(InventoryError, match="already used"):
        post_operation(db, company_id, _op("receipt", product_id, warehouse_a.id, "21"), "receipt-001", user_id, None)
    db.rollback()

    transfer = post_operation(db, company_id, _op("transfer", product_id, warehouse_a.id, "5", destination_warehouse_id=warehouse_b.id), "transfer-001", user_id, None)
    assert len(db.scalars(select(InventoryMovement).where(InventoryMovement.operation_id == transfer.id)).all()) == 2
    post_operation(db, company_id, _op("adjustment_positive", product_id, warehouse_b.id, "2", reason="physical count"), "adjust-001", user_id, None)
    post_operation(db, company_id, _op("adjustment_negative", product_id, warehouse_b.id, "1", reason="damage"), "adjust-002", user_id, None)
    post_operation(db, company_id, _op("issue", product_id, warehouse_a.id, "3"), "issue-001", user_id, None)
    balances = db.scalars(select(InventoryBalance).where(InventoryBalance.company_id == company_id)).all()
    quantities = {row.warehouse_id: row.quantity for row in balances}
    assert quantities[warehouse_a.id] == Decimal("12.000000")
    assert quantities[warehouse_b.id] == Decimal("6.000000")
    assert reconcile_balances(db, company_id) == []
    balances[0].quantity = Decimal("99")
    db.commit()
    assert len(reconcile_balances(db, company_id)) == 1
    assert len(reconcile_balances(db, company_id, repair=True)) == 1
    db.refresh(balances[0])
    assert balances[0].quantity == Decimal("12.000000")

    with pytest.raises(InventoryError, match="Insufficient stock"):
        post_operation(db, company_id, _op("issue", product_id, warehouse_b.id, "7"), "issue-too-much", user_id, None)
    db.rollback()
    assert db.scalar(select(InventoryOperation.id).where(InventoryOperation.idempotency_key == "issue-too-much")) is None
    with pytest.raises(InventoryError, match="Active inventory product"):
        post_operation(db, company_id, _op("receipt", foreign_product_id, warehouse_a.id, "1"), "foreign-product", user_id, None)
    db.rollback()
    with pytest.raises(InventoryError, match="Active warehouse"):
        post_operation(db, company_id, _op("receipt", product_id, foreign.id, "1"), "foreign-warehouse", user_id, None)
    db.rollback()
    with pytest.raises(InventoryError, match="nonzero stock"):
        deactivate_warehouse(db, company_id, warehouse_a.id, user_id, None)
    db.rollback()

    # Ledger is immutable even to direct ORM edits.
    movement = db.scalar(select(InventoryMovement).where(InventoryMovement.operation_id == receipt.id))
    movement.quantity = Decimal("99")
    with pytest.raises(Exception, match="immutable"):
        db.commit()
    db.rollback()
    receipt.kind = "issue"
    with pytest.raises(Exception, match="immutable"):
        db.commit()
    db.rollback()
    assert db.scalar(select(SecurityEvent.id).where(SecurityEvent.event_type == "inventory_operation_posted")) is not None


def test_fractional_quantity_policy_and_warehouse_deactivation(inventory_db):
    db, (company_id, _, user_id, product_id, _) = inventory_db
    warehouse = create_warehouse(db, company_id, WarehouseCreate(code="fractional", name="Fractional"), user_id, None)
    with pytest.raises(InventoryError, match="fractional"):
        post_operation(db, company_id, _op("receipt", product_id, warehouse.id, "1.25"), "fractional-no", user_id, None)
    db.rollback()
    db.get(Product, product_id).allows_fractional_quantity = True
    db.commit()
    post_operation(db, company_id, _op("receipt", product_id, warehouse.id, "1.25"), "fractional-yes", user_id, None)
    post_operation(db, company_id, _op("issue", product_id, warehouse.id, "1.25"), "fractional-issue", user_id, None)
    assert deactivate_warehouse(db, company_id, warehouse.id, user_id, None).is_active is False
