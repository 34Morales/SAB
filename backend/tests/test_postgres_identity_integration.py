"""Destructive integration suite: requires TEST_DATABASE_URL for a disposable PostgreSQL database."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from decimal import Decimal
import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.core.config import get_settings
from backend.app.modules.companies.models import Company
from backend.app.modules.identity.models import Permission, RecoveryEmailOutbox, Role, SecurityEvent, User
from backend.app.modules.identity.security import decrypt_secret, hash_password
from backend.app.modules.identity.service import (
    LoginFailure,
    authenticate,
    begin_mfa_setup,
    confirm_mfa_setup,
    request_password_recovery,
    reset_password,
    resolve_access,
)
from backend.app.modules.catalog.models import Product
from backend.app.modules.inventory.schemas import InventoryOperationRequest, WarehouseCreate
from backend.app.modules.inventory.service import InventoryError, create_warehouse, post_operation
from backend.app.modules.purchasing.schemas import InvoiceLineInput, PaymentCreate, SupplierCreate, SupplierInvoiceCreate
from backend.app.modules.purchasing.service import PurchasingError, create_supplier, create_supplier_invoice, post_supplier_payment
from backend.app.modules.purchasing.models import SupplierInvoice
from backend.app.modules.sales.schemas import CustomerCreate, OrderCreate, SalesLineInput
from backend.app.modules.sales.service import SalesError, create_customer as create_sales_customer, create_order as create_sales_order, transition_order as transition_sales_order


@pytest.fixture(scope="module")
def postgres_sessions():
    database_url = os.getenv("TEST_DATABASE_URL", "")
    if not database_url.startswith("postgresql+psycopg://"):
        pytest.skip("Set TEST_DATABASE_URL to a disposable PostgreSQL database using psycopg")

    schema = f"erp_test_{uuid4().hex}"
    admin_engine = create_engine(database_url)
    with admin_engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    test_engine = create_engine(database_url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        with test_engine.connect() as connection:
            alembic_config = Config("backend/alembic.ini")
            alembic_config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
            alembic_config.attributes["connection"] = connection
            command.upgrade(alembic_config, "head")
        yield sessionmaker(bind=test_engine, expire_on_commit=False)
    finally:
        test_engine.dispose()
        with admin_engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        admin_engine.dispose()


def _code_for_step(secret: str, step: int) -> str:
    import base64
    import hashlib
    import hmac
    import struct

    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    return f"{(struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7fffffff) % 1_000_000:06d}"


def test_postgres_migrations_mfa_recovery_throttle_and_audit(postgres_sessions, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("MFA_ENCRYPTION_KEY", key)
    get_settings.cache_clear()
    sessions = postgres_sessions

    with sessions.begin() as db:
        company = Company(legal_name="Postgres integration", country_code="MX")
        user = User(email=f"{uuid4().hex}@example.test", full_name="Integration User", password_hash=hash_password("integration-test-password"))
        role = db.scalar(select(Role).where(Role.name == "admin", Role.company_id.is_(None)))
        from backend.app.modules.identity.models import Membership

        db.add(Membership(company=company, user=user, roles=[role]))
        db.flush()
        custom_role = Role(company_id=company.id, name="integration-custom", is_system=False)
        db.add(custom_role)
        db.flush()
        assert custom_role.id > role.id
        security_permission = db.scalar(select(Permission).where(Permission.code == "security:audit:read"))
        custom_permission = Permission(code=f"integration:{uuid4().hex}", description="Sequence check")
        db.add(custom_permission)
        db.flush()
        assert custom_permission.id > security_permission.id
        user_id, company_id, email = user.id, company.id, user.email

    with sessions() as db:
        user = db.get(User, user_id)
        secret, _uri = begin_mfa_setup(db, user, company_id, "192.0.2.10")
    step = int(datetime.now(timezone.utc).timestamp()) // 30 + 1
    with sessions() as db:
        recovery_codes = confirm_mfa_setup(db, user_id, company_id, _code_for_step(secret, step), "192.0.2.10")
    assert len(recovery_codes) == 10

    with sessions() as db:
        token, _ = authenticate(db, email, "integration-test-password", "192.0.2.11", recovery_code=recovery_codes[0], company_id=company_id)
        assert resolve_access(db, token, company_id) is not None
        assert resolve_access(db, token, company_id + 1) is None

    def failed_attempt(_):
        with sessions() as db:
            try:
                authenticate(db, email, "wrong-password-value", "198.51.100.23", company_id=company_id)
            except LoginFailure as exc:
                return exc.reason

    with ThreadPoolExecutor(max_workers=5) as pool:
        outcomes = list(pool.map(failed_attempt, range(5)))
    assert "rate_limited" in outcomes

    with sessions() as db:
        request_password_recovery(db, email, "192.0.2.12")
        outbox = db.scalar(select(RecoveryEmailOutbox).where(RecoveryEmailOutbox.recipient == email))
        reset_token = decrypt_secret(outbox.encrypted_token)
        reset_password(db, reset_token, "new-integration-password", "192.0.2.12")
    with sessions() as db:
        assert db.scalar(select(SecurityEvent).where(SecurityEvent.company_id == company_id)) is not None
        with pytest.raises(Exception):
            event = db.scalar(select(SecurityEvent).where(SecurityEvent.company_id == company_id))
            event.event_type = "tampered"
            db.flush()


def test_postgres_inventory_concurrent_issue_cannot_overdraw(postgres_sessions):
    sessions = postgres_sessions
    with sessions.begin() as db:
        company = Company(legal_name="Inventory concurrency", country_code="MX")
        user = User(email=f"inventory-{uuid4().hex}@example.test", full_name="Inventory", password_hash=hash_password("inventory-test-password"))
        db.add_all([company, user])
        db.flush()
        product = Product(company_id=company.id, sku=f"P-{uuid4().hex[:12]}", name="Concurrent", kind="goods", unit_code="EA", price=Decimal("1"), tax_rate=Decimal("0"))
        db.add(product)
        db.flush()
        company_id, user_id, product_id = company.id, user.id, product.id
    with sessions() as db:
        warehouse = create_warehouse(db, company_id, WarehouseCreate(code=f"W-{uuid4().hex[:8]}", name="Concurrency"), user_id, None)
        warehouse_id = warehouse.id
        post_operation(db, company_id, InventoryOperationRequest(kind="receipt", product_id=product_id, warehouse_id=warehouse_id, quantity=Decimal("10"), reason="opening stock"), f"open-{uuid4().hex}", user_id, None)

    def issue(index: int) -> str:
        try:
            with sessions() as db:
                post_operation(db, company_id, InventoryOperationRequest(kind="issue", product_id=product_id, warehouse_id=warehouse_id, quantity=Decimal("7"), reason="concurrent sale"), f"issue-{uuid4().hex}-{index}", user_id, None)
            return "posted"
        except InventoryError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(issue, (1, 2)))
    assert sorted(outcomes) == ["posted", "rejected"]


def test_postgres_concurrent_supplier_payments_cannot_overpay(postgres_sessions):
    sessions = postgres_sessions
    with sessions.begin() as db:
        company = Company(legal_name="AP concurrency", country_code="MX")
        user = User(email=f"ap-{uuid4().hex}@example.test", full_name="AP Operator", password_hash=hash_password("ap-test-password"))
        db.add_all([company, user])
        db.flush()
        company_id, user_id = company.id, user.id
    with sessions() as db:
        supplier = create_supplier(db, company_id, SupplierCreate(code=f"AP-{uuid4().hex[:8]}", legal_name="Concurrent Payee"), user_id, None)
        invoice = create_supplier_invoice(db, company_id, SupplierInvoiceCreate(
            supplier_id=supplier.id, document_number=f"AP-{uuid4().hex[:12]}",
            issued_date=date(2026, 10, 1), due_date=date(2026, 10, 31), currency_code="MXN",
            lines=[InvoiceLineInput(description="Services", quantity=Decimal("1"), unit_price=Decimal("100.00"))],
        ), user_id, None)
        invoice_id = invoice.id

    def pay(index: int) -> str:
        try:
            with sessions() as db:
                post_supplier_payment(db, company_id, invoice_id, PaymentCreate(amount=Decimal("70.00"), paid_on=date(2026, 10, 2)), f"concurrent-pay-{uuid4().hex}-{index}", user_id, None)
            return "posted"
        except PurchasingError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(pay, (1, 2)))
    assert sorted(outcomes) == ["posted", "rejected"]
    with sessions() as db:
        invoice = db.get(SupplierInvoice, invoice_id)
        assert invoice.paid_amount == Decimal("70.00")


def test_postgres_concurrent_sales_reservations_cannot_oversell(postgres_sessions):
    sessions = postgres_sessions
    with sessions.begin() as db:
        company = Company(legal_name="Sales reservation concurrency", country_code="MX")
        user = User(email=f"sales-{uuid4().hex}@example.test", full_name="Sales Operator", password_hash=hash_password("sales-test-password"))
        db.add_all([company, user])
        db.flush()
        company_id, user_id = company.id, user.id
        product = Product(company_id=company_id, sku=f"RSV-{uuid4().hex[:8]}", name="Limited stock", kind="goods", unit_code="EA", price=Decimal("4.00"), tax_rate=Decimal("0"))
        db.add(product)
        db.flush()
        product_id = product.id
    with sessions() as db:
        customer = create_sales_customer(db, company_id, CustomerCreate(code=f"C-{uuid4().hex[:8]}", legal_name="Reservation customer"), user_id, None)
        warehouse = create_warehouse(db, company_id, WarehouseCreate(code=f"W-{uuid4().hex[:8]}", name="Reservation stock"), user_id, None)
        post_operation(db, company_id, InventoryOperationRequest(kind="receipt", product_id=product_id, warehouse_id=warehouse.id, quantity=Decimal("1"), reason="Single unit"), f"sales-stock-{uuid4().hex}", user_id, None)
        order_ids = [create_sales_order(db, company_id, OrderCreate(customer_id=customer.id, warehouse_id=warehouse.id,
            number=f"SO-{uuid4().hex[:10]}", currency_code="MXN", lines=[SalesLineInput(product_id=product_id, quantity=Decimal("1"), unit_price=Decimal("4"))]), user_id, None).id for _ in range(2)]

    def reserve(order_id: int) -> str:
        try:
            with sessions() as db:
                transition_sales_order(db, company_id, order_id, "confirm", user_id, None)
            return "reserved"
        except SalesError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, order_ids))
    assert sorted(results) == ["rejected", "reserved"]
