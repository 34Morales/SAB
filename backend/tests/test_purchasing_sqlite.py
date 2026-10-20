"""SQLite purchasing, receiving, returns and accounts-payable integration rules."""

from datetime import date
from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.modules.catalog.models import Product
from backend.app.modules.companies.models import Company
from backend.app.modules.identity.models import SecurityEvent, User
from backend.app.modules.identity.security import hash_password
from backend.app.modules.inventory.models import InventoryBalance, InventoryMovement, InventoryOperation, Warehouse
from backend.app.modules.inventory.schemas import WarehouseCreate
from backend.app.modules.inventory.service import create_warehouse
from backend.app.modules.purchasing.models import (
    PurchaseOrder, PurchaseOrderLine, PurchaseReceipt, PurchaseReceiptLine, Supplier, SupplierInvoice,
    SupplierPayment, SupplierReturn,
)
from backend.app.modules.purchasing.schemas import (
    InvoiceLineInput, PaymentCreate, PurchaseOrderCreate, PurchaseOrderLineInput, PurchaseOrderUpdate,
    ReceiptCreate, ReceiptLineInput, SupplierCreate, SupplierInvoiceCreate, SupplierReturnCreate,
    SupplierReturnLineInput,
)
from backend.app.modules.purchasing.service import (
    PurchasingError, create_purchase_order, create_supplier, create_supplier_invoice,
    create_supplier_return, get_purchase_order, list_suppliers, post_supplier_payment,
    receive_purchase_order, transition_purchase_order, update_purchase_order,
)
import backend.app.modules.purchasing.service as purchasing_service


@pytest.fixture
def purchasing_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with engine.connect() as connection:
        config = Config("backend/alembic.ini")
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as db:
        company = Company(legal_name="Purchasing tenant", country_code="MX")
        other = Company(legal_name="Other tenant", country_code="MX")
        user = User(email="purchasing@example.test", full_name="Purchasing Operator", password_hash=hash_password("purchasing-test-password"))
        db.add_all([company, other, user])
        db.flush()
        goods = Product(company_id=company.id, sku="PUR-G-1", name="Paper", kind="goods", unit_code="EA", price=Decimal("8.00"), tax_rate=Decimal("16"))
        service = Product(company_id=company.id, sku="PUR-S-1", name="Freight", kind="service", unit_code="EA", price=Decimal("5.00"), tax_rate=Decimal("0"))
        foreign = Product(company_id=other.id, sku="PUR-X-1", name="Foreign", kind="goods", unit_code="EA", price=Decimal("1.00"), tax_rate=Decimal("0"))
        db.add_all([goods, service, foreign])
        db.flush()
        ids = (company.id, other.id, user.id, goods.id, service.id, foreign.id)
    try:
        with factory() as db:
            yield db, ids
    finally:
        engine.dispose()


def _order_data(supplier_id: int, warehouse_id: int, goods_id: int, service_id: int):
    return PurchaseOrderCreate(
        supplier_id=supplier_id, warehouse_id=warehouse_id, number="PO-1001", currency_code="MXN",
        lines=[
            PurchaseOrderLineInput(product_id=goods_id, quantity=Decimal("3"), unit_price=Decimal("10.005"), discount_percent=Decimal("10"), tax_rate=Decimal("16")),
            PurchaseOrderLineInput(product_id=service_id, quantity=Decimal("1"), unit_price=Decimal("5"), tax_rate=Decimal("0")),
        ],
    )


def test_order_receipts_returns_invoice_and_partial_payments(purchasing_db):
    db, (company_id, _, user_id, goods_id, service_id, _) = purchasing_db
    supplier = create_supplier(db, company_id, SupplierCreate(code="s-1", legal_name="Supplier One", email="ap@supplier.test", payment_terms_days=30, preferred_currency="MXN"), user_id, None)
    warehouse = create_warehouse(db, company_id, WarehouseCreate(code="buy", name="Receiving"), user_id, None)
    assert [s.id for s in list_suppliers(db, company_id, 20, 0, search="supplier")] == [supplier.id]
    order = create_purchase_order(db, company_id, _order_data(supplier.id, warehouse.id, goods_id, service_id), user_id, None)
    assert (order.subtotal, order.tax_total, order.total) == (Decimal("32.01"), Decimal("4.32"), Decimal("36.33"))
    assert order.status == "draft"
    order = update_purchase_order(db, company_id, order.id, PurchaseOrderUpdate(notes="approved for delivery"), user_id, None)
    assert order.notes == "approved for delivery"
    order = transition_purchase_order(db, company_id, order.id, "confirm", user_id, None)
    assert order.status == "confirmed"
    with pytest.raises(PurchasingError, match="Only draft"):
        update_purchase_order(db, company_id, order.id, PurchaseOrderUpdate(notes="silent edit"), user_id, None)
    db.rollback()

    goods_line, service_line = order.lines
    first = ReceiptCreate(notes="one accepted, one damaged", lines=[
        ReceiptLineInput(purchase_order_line_id=goods_line.id, accepted_quantity=Decimal("1"), damaged_quantity=Decimal("1"), notes="box dented"),
    ])
    receipt1 = receive_purchase_order(db, company_id, order.id, first, "recv-1", user_id, None)
    retry = receive_purchase_order(db, company_id, order.id, first, "recv-1", user_id, None)
    assert retry.id == receipt1.id
    assert receipt1.lines[0].inventory_operation_id is not None
    assert receipt1.lines[0].damaged_quantity == Decimal("1.000000")
    assert db.scalar(select(InventoryBalance.quantity).where(InventoryBalance.company_id == company_id, InventoryBalance.product_id == goods_id, InventoryBalance.warehouse_id == warehouse.id)) == Decimal("1.000000")
    operation = db.get(InventoryOperation, receipt1.lines[0].inventory_operation_id)
    assert operation.reference_type == "purchase_receipt" and operation.reference_id == str(receipt1.id)
    with pytest.raises(PurchasingError, match="with receipts cannot be cancelled"):
        transition_purchase_order(db, company_id, order.id, "cancel", user_id, None)
    db.rollback()
    with pytest.raises(PurchasingError, match="already used"):
        receive_purchase_order(db, company_id, order.id, ReceiptCreate(lines=[ReceiptLineInput(purchase_order_line_id=goods_line.id, accepted_quantity=Decimal("2"))]), "recv-1", user_id, None)
    db.rollback()

    receipt2 = receive_purchase_order(db, company_id, order.id, ReceiptCreate(lines=[
        ReceiptLineInput(purchase_order_line_id=goods_line.id, accepted_quantity=Decimal("1")),
    ]), "recv-2", user_id, None)
    order = get_purchase_order(db, company_id, order.id)
    assert order.lines[0].received_quantity == Decimal("3.000000")
    order = transition_purchase_order(db, company_id, order.id, "close", user_id, None)
    assert order.status == "closed"

    returned = create_supplier_return(db, company_id, receipt1.id, SupplierReturnCreate(reason="Damaged packaging", lines=[
        SupplierReturnLineInput(receipt_line_id=receipt1.lines[0].id, quantity=Decimal("1")),
    ]), "return-1", user_id, None)
    returned_retry = create_supplier_return(db, company_id, receipt1.id, SupplierReturnCreate(reason="Damaged packaging", lines=[
        SupplierReturnLineInput(receipt_line_id=receipt1.lines[0].id, quantity=Decimal("1")),
    ]), "return-1", user_id, None)
    assert returned.id == returned_retry.id
    assert db.scalar(select(InventoryBalance.quantity).where(InventoryBalance.company_id == company_id, InventoryBalance.product_id == goods_id, InventoryBalance.warehouse_id == warehouse.id)) == Decimal("1.000000")
    with pytest.raises(PurchasingError, match="exceeds the accepted"):
        create_supplier_return(db, company_id, receipt2.id, SupplierReturnCreate(reason="Return too much", lines=[
            SupplierReturnLineInput(receipt_line_id=receipt2.lines[0].id, quantity=Decimal("2")),
        ]), "return-too-much", user_id, None)
    db.rollback()

    invoice = create_supplier_invoice(db, company_id, SupplierInvoiceCreate(
        supplier_id=supplier.id, document_number="SUP-INV-01", purchase_order_id=order.id,
        receipt_id=receipt1.id, issued_date=date(2026, 10, 1), due_date=date(2026, 10, 31),
        currency_code="MXN",
        lines=[InvoiceLineInput(description="Paper and freight", quantity=Decimal("2"), unit_price=Decimal("10"), tax_rate=Decimal("16"), purchase_order_line_id=goods_line.id, receipt_line_id=receipt1.lines[0].id)],
    ), user_id, None)
    assert invoice.subtotal == Decimal("20.00") and invoice.tax_total == Decimal("3.20") and invoice.total == Decimal("23.20")
    other_supplier = create_supplier(db, company_id, SupplierCreate(code="s-2", legal_name="Different supplier"), user_id, None)
    with pytest.raises(PurchasingError, match="not in this supplier"):
        create_supplier_invoice(db, company_id, SupplierInvoiceCreate(
            supplier_id=other_supplier.id, document_number="WRONG-PO-LINE",
            issued_date=date(2026, 10, 1), due_date=date(2026, 10, 31), currency_code="MXN",
            lines=[InvoiceLineInput(description="Cross supplier", quantity=Decimal("1"), unit_price=Decimal("1"), purchase_order_line_id=goods_line.id)],
        ), user_id, None)
    db.rollback()
    with pytest.raises(IntegrityError):
        create_supplier_invoice(db, company_id, SupplierInvoiceCreate(
            supplier_id=supplier.id, document_number="SUP-INV-01", issued_date=date(2026, 10, 1), due_date=date(2026, 10, 31),
            currency_code="MXN", lines=[InvoiceLineInput(description="duplicate", quantity=Decimal("1"), unit_price=Decimal("1"))],
        ), user_id, None)
    db.rollback()
    payment1 = post_supplier_payment(db, company_id, invoice.id, PaymentCreate(amount=Decimal("10.00"), paid_on=date(2026, 10, 2)), "pay-1", user_id, None)
    assert post_supplier_payment(db, company_id, invoice.id, PaymentCreate(amount=Decimal("10.00"), paid_on=date(2026, 10, 2)), "pay-1", user_id, None).id == payment1.id
    invoice = db.get(SupplierInvoice, invoice.id)
    assert invoice.status == "partial" and invoice.total - invoice.paid_amount == Decimal("13.20")
    payment2 = post_supplier_payment(db, company_id, invoice.id, PaymentCreate(amount=Decimal("13.20"), paid_on=date(2026, 10, 3)), "pay-2", user_id, None)
    assert payment2.amount == Decimal("13.20") and db.get(SupplierInvoice, invoice.id).status == "paid"
    assert db.scalar(select(SecurityEvent.id).where(SecurityEvent.event_type == "supplier_payment_posted")) is not None


def test_scope_and_atomic_rollback_across_inventory_operations(purchasing_db, monkeypatch):
    db, (company_id, other_company_id, user_id, goods_id, service_id, foreign_id) = purchasing_db
    supplier = create_supplier(db, company_id, SupplierCreate(code="rollback-s", legal_name="Rollback supplier"), user_id, None)
    other_supplier = create_supplier(db, other_company_id, SupplierCreate(code="foreign-s", legal_name="Foreign supplier"), user_id, None)
    warehouse = create_warehouse(db, company_id, WarehouseCreate(code="rollback", name="Rollback"), user_id, None)
    with pytest.raises(PurchasingError):
        create_purchase_order(db, company_id, PurchaseOrderCreate(
            supplier_id=other_supplier.id, warehouse_id=warehouse.id, number="BAD-1", currency_code="MXN",
            lines=[PurchaseOrderLineInput(product_id=goods_id, quantity=Decimal("1"), unit_price=Decimal("1"))],
        ), user_id, None)
    db.rollback()
    with pytest.raises(PurchasingError):
        create_purchase_order(db, company_id, PurchaseOrderCreate(
            supplier_id=supplier.id, warehouse_id=warehouse.id, number="BAD-2", currency_code="MXN",
            lines=[PurchaseOrderLineInput(product_id=foreign_id, quantity=Decimal("1"), unit_price=Decimal("1"))],
        ), user_id, None)
    db.rollback()
    order = create_purchase_order(db, company_id, PurchaseOrderCreate(
        supplier_id=supplier.id, warehouse_id=warehouse.id, number="ROLL-1", currency_code="MXN",
        lines=[
            PurchaseOrderLineInput(product_id=goods_id, quantity=Decimal("1"), unit_price=Decimal("1")),
            PurchaseOrderLineInput(product_id=goods_id, quantity=Decimal("1"), unit_price=Decimal("1")),
        ],
    ), user_id, None)
    order = transition_purchase_order(db, company_id, order.id, "confirm", user_id, None)
    original = purchasing_service.post_inventory_operation
    calls = {"count": 0}

    def fail_on_second(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("injected mid-transaction failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(purchasing_service, "post_inventory_operation", fail_on_second)
    with pytest.raises(RuntimeError, match="injected"):
        purchasing_service.receive_purchase_order(
            db, company_id, order.id,
            ReceiptCreate(lines=[
                ReceiptLineInput(purchase_order_line_id=order.lines[0].id, accepted_quantity=Decimal("1")),
                ReceiptLineInput(purchase_order_line_id=order.lines[1].id, accepted_quantity=Decimal("1")),
            ]), "atomic-receipt", user_id, None,
        )
    db.rollback()
    assert db.scalar(select(PurchaseReceipt.id).where(PurchaseReceipt.idempotency_key == "atomic-receipt")) is None
    assert db.scalar(select(InventoryMovement.id).where(InventoryMovement.company_id == company_id)) is None
    assert [line.received_quantity for line in get_purchase_order(db, company_id, order.id).lines] == [Decimal("0.000000"), Decimal("0.000000")]
    assert transition_purchase_order(db, company_id, order.id, "cancel", user_id, None).status == "cancelled"

