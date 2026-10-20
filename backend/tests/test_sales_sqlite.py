"""SQLite end-to-end sales rules, reservations, delivery and receivables."""

from datetime import date
from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.modules.catalog.models import Product
from backend.app.modules.companies.models import Company
from backend.app.modules.identity.models import SecurityEvent, User
from backend.app.modules.identity.security import hash_password
from backend.app.modules.inventory.models import InventoryBalance, InventoryMovement
from backend.app.modules.inventory.schemas import InventoryOperationRequest, WarehouseCreate
from backend.app.modules.inventory.service import InventoryError, create_warehouse, post_operation
from backend.app.modules.sales.models import Customer, SalesCreditNote, SalesInvoice, SalesOrder
from backend.app.modules.sales.schemas import (
    CustomerCreate, DeliveryCreate, DeliveryLineInput, InvoiceCreate, OrderCreate,
    PaymentCreate, QuoteCreate, ReturnCreate, SalesLineInput,
)
from backend.app.modules.sales.service import (
    SalesError, create_customer, create_delivery, create_invoice, create_quote, create_return,
    convert_quote, get_invoice, list_customers, post_payment, transition_order,
    transition_quote,
)


@pytest.fixture
def sales_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with engine.connect() as connection:
        config = Config("backend/alembic.ini")
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as db:
        company = Company(legal_name="Sales Company", country_code="MX")
        other = Company(legal_name="Other Sales Company", country_code="MX")
        user = User(email="sales@example.test", full_name="Sales User", password_hash=hash_password("sales-test-password"))
        db.add_all([company, other, user])
        db.flush()
        product = Product(company_id=company.id, sku="SALE-1", name="Widget", kind="goods", unit_code="EA", price=Decimal("10.005"), tax_rate=Decimal("16"))
        foreign_product = Product(company_id=other.id, sku="SALE-X", name="Foreign", kind="goods", unit_code="EA", price=Decimal("1"), tax_rate=Decimal("0"))
        db.add_all([product, foreign_product])
        db.flush()
        ids = (company.id, other.id, user.id, product.id, foreign_product.id)
    try:
        with factory() as db:
            yield db, ids
    finally:
        engine.dispose()


def _seed_stock(db, company_id, user_id, product_id):
    warehouse = create_warehouse(db, company_id, WarehouseCreate(code="OUT", name="Outbound"), user_id, None)
    post_operation(db, company_id, InventoryOperationRequest(kind="receipt", product_id=product_id,
        warehouse_id=warehouse.id, quantity=Decimal("5"), reason="Opening stock"), "opening-sales-stock", user_id, None)
    return warehouse


def test_quote_reservation_partial_delivery_return_invoice_and_payment(sales_db):
    db, (company_id, other_company_id, user_id, product_id, foreign_product_id) = sales_db
    customer = create_customer(db, company_id, CustomerCreate(code="c-1", legal_name="Customer One", credit_limit=Decimal("100")), user_id, None)
    warehouse = _seed_stock(db, company_id, user_id, product_id)
    assert [row.id for row in list_customers(db, company_id, 10, 0, "customer", True)] == [customer.id]
    quote = create_quote(db, company_id, QuoteCreate(customer_id=customer.id, number="Q-1", currency_code="MXN",
        valid_until=date(2026, 12, 31), lines=[SalesLineInput(product_id=product_id, quantity=Decimal("3"), unit_price=Decimal("10.005"), discount_percent=Decimal("10"), tax_rate=Decimal("16"))]), user_id, None)
    assert quote.status == "draft" and quote.subtotal == Decimal("27.01") and quote.tax_total == Decimal("4.32")
    assert db.scalar(select(InventoryBalance.quantity).where(InventoryBalance.product_id == product_id, InventoryBalance.warehouse_id == warehouse.id)) == Decimal("5.000000")
    with pytest.raises(SalesError, match="Active product"):
        create_quote(db, company_id, QuoteCreate(customer_id=customer.id, number="Q-CROSS", currency_code="MXN",
            lines=[SalesLineInput(product_id=foreign_product_id, quantity=Decimal("1"))]), user_id, None)
    db.rollback()
    quote = transition_quote(db, company_id, quote.id, "send", user_id, None)
    quote = transition_quote(db, company_id, quote.id, "accept", user_id, None)
    order = convert_quote(db, company_id, quote.id, "SO-1", warehouse.id, None, user_id, None)
    assert order.status == "draft" and order.total == quote.total and order.lines[0].quantity == quote.lines[0].quantity
    with pytest.raises(SalesError, match="Only accepted"):
        convert_quote(db, company_id, quote.id, "SO-duplicate", warehouse.id, None, user_id, None)
    db.rollback()
    order = transition_order(db, company_id, order.id, "confirm", user_id, None)
    assert order.status == "confirmed"
    with pytest.raises(SalesError, match="Insufficient available stock"):
        transition_order(db, company_id, create_direct_order(db, company_id, customer.id, warehouse.id, product_id, user_id, "SO-2").id, "confirm", user_id, None)
    db.rollback()
    with pytest.raises(InventoryError, match="sales reservations"):
        post_operation(db, company_id, InventoryOperationRequest(kind="issue", product_id=product_id, warehouse_id=warehouse.id, quantity=Decimal("3"), reason="Manual issue"), "manual-over-reserve", user_id, None)
    db.rollback()
    low_credit_customer = create_customer(db, company_id, CustomerCreate(code="LOW-CREDIT", legal_name="Low credit", credit_limit=Decimal("0.00")), user_id, None)
    low_credit_order = create_direct_order(db, company_id, low_credit_customer.id, warehouse.id, product_id, user_id, "SO-LOW", quantity=Decimal("1"))
    with pytest.raises(SalesError, match="credit limit"):
        transition_order(db, company_id, low_credit_order.id, "confirm", user_id, None)
    db.rollback()
    first = create_delivery(db, company_id, order.id, DeliveryCreate(lines=[DeliveryLineInput(order_line_id=order.lines[0].id, quantity=Decimal("1"))]), "ship-1", user_id, None)
    assert create_delivery(db, company_id, order.id, DeliveryCreate(lines=[DeliveryLineInput(order_line_id=order.lines[0].id, quantity=Decimal("1"))]), "ship-1", user_id, None).id == first.id
    post_operation(db, company_id, InventoryOperationRequest(kind="issue", product_id=product_id, warehouse_id=warehouse.id, quantity=Decimal("2"), reason="Consume unreserved"), "manual-unreserved", user_id, None)
    order = db.get(SalesOrder, order.id)
    remaining = Decimal("2")
    second = create_delivery(db, company_id, order.id, DeliveryCreate(lines=[DeliveryLineInput(order_line_id=order.lines[0].id, quantity=remaining)]), "ship-2", user_id, None)
    assert second.lines[0].inventory_operation_id is not None
    assert db.get(SalesOrder, order.id).status == "fulfilled"
    invoice = create_invoice(db, company_id, InvoiceCreate(order_id=order.id, number="INV-1", issued_date=date(2026, 10, 1), due_date=date(2026, 10, 31)), user_id, None)
    assert invoice.total == Decimal("31.33") and invoice.lines[0].quantity == Decimal("3.000000")
    with pytest.raises(SalesError, match="No fulfilled unbilled"):
        create_invoice(db, company_id, InvoiceCreate(order_id=order.id, number="INV-2", issued_date=date(2026, 10, 1), due_date=date(2026, 10, 31)), user_id, None)
    db.rollback()
    payment = post_payment(db, company_id, invoice.id, PaymentCreate(amount=Decimal("10.00"), paid_on=date(2026, 10, 2)), "sale-pay-1", user_id, None)
    assert payment.amount == Decimal("10.00")
    returned = create_return(db, company_id, ReturnCreate(delivery_line_id=first.lines[0].id, quantity=Decimal("1"), reason="Customer return", invoice_id=invoice.id), "sale-return-1", user_id, None)
    assert returned.inventory_operation_id is not None
    assert create_return(db, company_id, ReturnCreate(delivery_line_id=first.lines[0].id, quantity=Decimal("1"), reason="Customer return", invoice_id=invoice.id), "sale-return-1", user_id, None).id == returned.id
    assert db.scalar(select(SalesCreditNote.id).where(SalesCreditNote.return_id == returned.id)) is not None
    invoice = get_invoice(db, company_id, invoice.id)
    assert invoice.credited_amount == Decimal("10.44") and invoice.total - invoice.paid_amount - invoice.credited_amount == Decimal("10.89")
    with pytest.raises(SalesError, match="exceeds delivered"):
        create_return(db, company_id, ReturnCreate(delivery_line_id=first.lines[0].id, quantity=Decimal("1"), reason="duplicate qty"), "sale-return-2", user_id, None)
    db.rollback()
    assert db.scalar(select(InventoryMovement.id).where(InventoryMovement.company_id == other_company_id)) is None
    assert db.scalar(select(SecurityEvent.id).where(SecurityEvent.company_id == company_id, SecurityEvent.event_type == "sales_payment_posted")) is not None
    assert db.scalar(select(Customer.id).where(Customer.id == customer.id, Customer.company_id == other_company_id)) is None
    assert db.scalar(select(SalesInvoice.id).where(SalesInvoice.id == invoice.id, SalesInvoice.company_id == company_id)) is not None
    reserved_order = create_direct_order(db, company_id, customer.id, warehouse.id, product_id, user_id, "SO-CANCEL", quantity=Decimal("1"))
    assert transition_order(db, company_id, reserved_order.id, "confirm", user_id, None).status == "confirmed"
    assert transition_order(db, company_id, reserved_order.id, "cancel", user_id, None).status == "cancelled"
    released = create_direct_order(db, company_id, customer.id, warehouse.id, product_id, user_id, "SO-RELEASED", quantity=Decimal("1"))
    assert transition_order(db, company_id, released.id, "confirm", user_id, None).status == "confirmed"


def create_direct_order(db, company_id, customer_id, warehouse_id, product_id, user_id, number, quantity=Decimal("3")):
    from backend.app.modules.sales.service import create_order
    return create_order(db, company_id, OrderCreate(customer_id=customer_id, warehouse_id=warehouse_id, number=number,
        currency_code="MXN", lines=[SalesLineInput(product_id=product_id, quantity=quantity, unit_price=Decimal("1"))]), user_id, None)
