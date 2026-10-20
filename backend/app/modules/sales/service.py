"""Sales rules; stock reservations, deliveries, returns and receivables are transactional."""

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, selectinload

from backend.app.modules.catalog.models import Product
from backend.app.modules.companies.models import Branch
from backend.app.modules.identity.models import SecurityEvent
from backend.app.modules.inventory.models import InventoryBalance, InventoryLocation, InventoryOperation, Warehouse
from backend.app.modules.inventory.schemas import InventoryOperationRequest
from backend.app.modules.inventory.service import InventoryError, lock_stock_pool, post_operation
from backend.app.modules.sales.models import (
    Customer, Quote, QuoteLine, SalesCreditNote, SalesDelivery, SalesDeliveryLine,
    SalesEvent, SalesInvoice, SalesInvoiceLine, SalesOrder, SalesOrderLine,
    SalesPayment, SalesReturn,
)
from backend.app.modules.sales.schemas import (
    CustomerCreate, CustomerUpdate, DeliveryCreate, InvoiceCreate, OrderCreate,
    QuoteCreate, QuoteUpdate, ReturnCreate, SalesLineInput, PaymentCreate,
)

CENT = Decimal("0.01")
ZERO = Decimal("0")
OPEN_ORDER_STATES = ("confirmed", "partially_fulfilled")


class SalesError(Exception):
    """Expected sales domain failure safe for an API response."""


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _hash(payload: object) -> str:
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _audit(db: Session, company_id: int, actor_id: int, event: str, ip: str | None, details: dict) -> None:
    db.add(SecurityEvent(user_id=actor_id, company_id=company_id, event_type=event, source_ip=ip, details=details))


def _event(db: Session, company_id: int, actor_id: int, event: str, ip: str | None, *, order_id=None, quote_id=None, details=None) -> None:
    db.add(SalesEvent(company_id=company_id, order_id=order_id, quote_id=quote_id, actor_user_id=actor_id, event_type=event, details=details or {}))
    _audit(db, company_id, actor_id, event, ip, details or {})


def list_customers(db: Session, company_id: int, limit: int, offset: int, search: str | None, active: bool | None):
    q = select(Customer).where(Customer.company_id == company_id)
    if active is not None:
        q = q.where(Customer.is_active.is_(active))
    if search:
        term = f"%{search.strip()}%"
        q = q.where(Customer.legal_name.ilike(term) | Customer.code.ilike(term) | Customer.email.ilike(term))
    return list(db.scalars(q.order_by(Customer.legal_name, Customer.id).offset(offset).limit(limit)))


def get_customer(db: Session, company_id: int, customer_id: int):
    return db.scalar(select(Customer).where(Customer.company_id == company_id, Customer.id == customer_id))


def create_customer(db: Session, company_id: int, data: CustomerCreate, actor_id: int, ip: str | None):
    customer = Customer(company_id=company_id, **data.model_dump(exclude={"code"}), code=data.code.strip().upper())
    db.add(customer)
    db.flush()
    _audit(db, company_id, actor_id, "customer_created", ip, {"customer_id": customer.id, "code": customer.code})
    db.commit()
    db.refresh(customer)
    return customer


def update_customer(db: Session, company_id: int, customer_id: int, data: CustomerUpdate, actor_id: int, ip: str | None):
    customer = db.scalar(select(Customer).where(Customer.company_id == company_id, Customer.id == customer_id).with_for_update())
    if customer is None:
        return None
    changes = data.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(customer, key, value)
    _audit(db, company_id, actor_id, "customer_updated", ip, {"customer_id": customer_id, "changed_fields": sorted(changes)})
    db.commit()
    db.refresh(customer)
    return customer


def _make_lines(db: Session, company_id: int, entries: list[SalesLineInput], line_model, parent_field: str, parent_id: int):
    lines, subtotal, tax_total = [], ZERO, ZERO
    for entry in entries:
        product = db.scalar(select(Product).where(Product.id == entry.product_id, Product.company_id == company_id, Product.is_active.is_(True)))
        if product is None:
            raise SalesError("Active product not found in company")
        if not product.allows_fractional_quantity and entry.quantity != entry.quantity.to_integral_value():
            raise SalesError("Product does not allow fractional quantities")
        unit_price = entry.unit_price if entry.unit_price is not None else product.price
        tax_rate = entry.tax_rate if entry.tax_rate is not None else product.tax_rate
        desc = (entry.description or product.name).strip()
        if not desc:
            raise SalesError("Sales line description cannot be blank")
        net = _money(entry.quantity * unit_price * (Decimal("1") - entry.discount_percent / Decimal("100")))
        tax = _money(net * tax_rate / Decimal("100"))
        fields = {"product_id": product.id, "description": desc, "quantity": entry.quantity,
                  "unit_price": unit_price, "discount_percent": entry.discount_percent, "tax_rate": tax_rate,
                  "subtotal": net, "tax_amount": tax, "total": net + tax}
        if line_model is SalesOrderLine:
            fields["fulfilled_quantity"] = Decimal("0")
        lines.append(line_model(**{parent_field: parent_id}, **fields))
        subtotal += net
        tax_total += tax
    return lines, subtotal, tax_total


def create_quote(db: Session, company_id: int, data: QuoteCreate, actor_id: int, ip: str | None):
    customer = db.scalar(select(Customer).where(Customer.id == data.customer_id, Customer.company_id == company_id, Customer.is_active.is_(True)))
    if customer is None:
        raise SalesError("Active customer not found in company")
    quote = Quote(company_id=company_id, customer_id=customer.id, number=data.number.strip(), currency_code=data.currency_code,
                  valid_until=data.valid_until, notes=data.notes, subtotal=ZERO, tax_total=ZERO, total=ZERO, created_by_user_id=actor_id)
    db.add(quote)
    db.flush()
    lines, subtotal, tax = _make_lines(db, company_id, data.lines, QuoteLine, "quote_id", quote.id)
    quote.lines, quote.subtotal, quote.tax_total, quote.total = lines, subtotal, tax, subtotal + tax
    _event(db, company_id, actor_id, "sales_quote_created", ip, quote_id=quote.id, details={"quote_id": quote.id, "number": quote.number})
    db.commit()
    return get_quote(db, company_id, quote.id)


def get_quote(db: Session, company_id: int, quote_id: int):
    return db.scalar(select(Quote).where(Quote.id == quote_id, Quote.company_id == company_id).options(selectinload(Quote.lines)))


def list_quotes(db: Session, company_id: int, limit: int, offset: int, status: str | None, customer_id: int | None):
    q = select(Quote).where(Quote.company_id == company_id).options(selectinload(Quote.lines))
    if status:
        q = q.where(Quote.status == status)
    if customer_id:
        q = q.where(Quote.customer_id == customer_id)
    return list(db.scalars(q.order_by(Quote.id.desc()).offset(offset).limit(limit)))


def update_quote(db: Session, company_id: int, quote_id: int, data: QuoteUpdate, actor_id: int, ip: str | None):
    quote = db.scalar(select(Quote).where(Quote.id == quote_id, Quote.company_id == company_id).options(selectinload(Quote.lines)).with_for_update())
    if quote is None:
        return None
    if quote.status != "draft":
        raise SalesError("Only draft quotes can be edited")
    changes = data.model_dump(exclude_unset=True)
    if "customer_id" in changes and db.scalar(select(Customer.id).where(Customer.id == changes["customer_id"], Customer.company_id == company_id, Customer.is_active.is_(True))) is None:
        raise SalesError("Active customer not found in company")
    for key in ("customer_id", "currency_code", "valid_until", "notes"):
        if key in changes:
            setattr(quote, key, changes[key])
    if "lines" in changes:
        quote.lines, quote.subtotal, quote.tax_total = _make_lines(db, company_id, data.lines, QuoteLine, "quote_id", quote.id)
    if "lines" not in changes:
        quote.subtotal = sum((line.subtotal for line in quote.lines), ZERO)
        quote.tax_total = sum((line.tax_amount for line in quote.lines), ZERO)
    quote.total = quote.subtotal + quote.tax_total
    _event(db, company_id, actor_id, "sales_quote_updated", ip, quote_id=quote.id, details={"quote_id": quote.id, "fields": sorted(changes)})
    db.commit()
    return get_quote(db, company_id, quote.id)


def transition_quote(db: Session, company_id: int, quote_id: int, action: str, actor_id: int, ip: str | None):
    quote = db.scalar(select(Quote).where(Quote.id == quote_id, Quote.company_id == company_id).with_for_update())
    if quote is None:
        return None
    if action == "send" and quote.status == "draft":
        quote.status = "sent"
    elif action == "accept" and quote.status == "sent":
        if quote.valid_until and quote.valid_until < date.today():
            quote.status = "expired"
            _event(db, company_id, actor_id, "sales_quote_expired", ip, quote_id=quote.id, details={"quote_id": quote.id})
            db.commit()
            return get_quote(db, company_id, quote.id)
        quote.status = "accepted"
    elif action == "reject" and quote.status == "sent":
        quote.status = "rejected"
    else:
        raise SalesError("Invalid quote state transition")
    _event(db, company_id, actor_id, f"sales_quote_{quote.status}", ip, quote_id=quote.id, details={"quote_id": quote.id, "status": quote.status})
    db.commit()
    return get_quote(db, company_id, quote.id)


def _validate_order_scope(db: Session, company_id: int, customer_id: int, branch_id: int | None, warehouse_id: int):
    if db.scalar(select(Customer.id).where(Customer.id == customer_id, Customer.company_id == company_id, Customer.is_active.is_(True))) is None:
        raise SalesError("Active customer not found in company")
    warehouse = db.scalar(select(Warehouse).where(Warehouse.id == warehouse_id, Warehouse.company_id == company_id, Warehouse.is_active.is_(True)))
    if warehouse is None:
        raise SalesError("Active source warehouse not found in company")
    if branch_id is not None and db.scalar(select(Branch.id).where(Branch.id == branch_id, Branch.company_id == company_id, Branch.is_active.is_(True))) is None:
        raise SalesError("Branch not found in company")
    if warehouse.branch_id is not None and warehouse.branch_id != branch_id:
        raise SalesError("Source warehouse does not belong to selected branch")


def create_order(db: Session, company_id: int, data: OrderCreate, actor_id: int, ip: str | None, quote_id: int | None = None, copied_lines=None):
    _validate_order_scope(db, company_id, data.customer_id, data.branch_id, data.warehouse_id)
    order = SalesOrder(company_id=company_id, customer_id=data.customer_id, quote_id=quote_id,
        branch_id=data.branch_id, warehouse_id=data.warehouse_id, number=data.number.strip(), status="draft",
        currency_code=data.currency_code, notes=data.notes, subtotal=ZERO, tax_total=ZERO, total=ZERO, created_by_user_id=actor_id)
    db.add(order)
    db.flush()
    if copied_lines is None:
        lines, subtotal, tax = _make_lines(db, company_id, data.lines, SalesOrderLine, "order_id", order.id)
    else:
        lines = [SalesOrderLine(order_id=order.id, product_id=line.product_id, description=line.description,
            quantity=line.quantity, fulfilled_quantity=ZERO, unit_price=line.unit_price, discount_percent=line.discount_percent,
            tax_rate=line.tax_rate, subtotal=line.subtotal, tax_amount=line.tax_amount, total=line.total) for line in copied_lines]
        subtotal, tax = sum((line.subtotal for line in lines), ZERO), sum((line.tax_amount for line in lines), ZERO)
    order.lines, order.subtotal, order.tax_total, order.total = lines, subtotal, tax, subtotal + tax
    db.flush()
    _event(db, company_id, actor_id, "sales_order_created", ip, order_id=order.id, quote_id=quote_id, details={"order_id": order.id, "number": order.number, "quote_id": quote_id})
    db.commit()
    return get_order(db, company_id, order.id)


def convert_quote(db: Session, company_id: int, quote_id: int, number: str, warehouse_id: int, branch_id: int | None, actor_id: int, ip: str | None):
    quote = db.scalar(select(Quote).where(Quote.id == quote_id, Quote.company_id == company_id).options(selectinload(Quote.lines)).with_for_update())
    if quote is None:
        return None
    if quote.status != "accepted":
        raise SalesError("Only accepted quotes can become orders")
    _validate_order_scope(db, company_id, quote.customer_id, branch_id, warehouse_id)
    order = SalesOrder(company_id=company_id, customer_id=quote.customer_id, quote_id=quote.id,
        branch_id=branch_id, warehouse_id=warehouse_id, number=number.strip(), status="draft",
        currency_code=quote.currency_code, notes=quote.notes, subtotal=quote.subtotal,
        tax_total=quote.tax_total, total=quote.total, created_by_user_id=actor_id)
    db.add(order)
    db.flush()
    order.lines = [SalesOrderLine(order_id=order.id, product_id=line.product_id, description=line.description,
        quantity=line.quantity, fulfilled_quantity=ZERO, unit_price=line.unit_price, discount_percent=line.discount_percent,
        tax_rate=line.tax_rate, subtotal=line.subtotal, tax_amount=line.tax_amount, total=line.total) for line in quote.lines]
    quote.status = "converted"
    _event(db, company_id, actor_id, "sales_order_created", ip, order_id=order.id, quote_id=quote.id, details={"order_id": order.id, "number": order.number, "quote_id": quote.id})
    _event(db, company_id, actor_id, "sales_quote_converted", ip, order_id=order.id, quote_id=quote.id, details={"quote_id": quote.id, "order_id": order.id})
    db.commit()
    return get_order(db, company_id, order.id)


def get_order(db: Session, company_id: int, order_id: int):
    return db.scalar(select(SalesOrder).where(SalesOrder.id == order_id, SalesOrder.company_id == company_id).options(selectinload(SalesOrder.lines)))


def list_orders(db: Session, company_id: int, limit: int, offset: int, status: str | None, customer_id: int | None):
    q = select(SalesOrder).where(SalesOrder.company_id == company_id).options(selectinload(SalesOrder.lines))
    if status:
        q = q.where(SalesOrder.status == status)
    if customer_id:
        q = q.where(SalesOrder.customer_id == customer_id)
    return list(db.scalars(q.order_by(SalesOrder.id.desc()).offset(offset).limit(limit)))


def _active_reserved(db: Session, company_id: int, product_id: int, warehouse_id: int, except_order_id: int | None = None) -> Decimal:
    q = select(func.coalesce(func.sum(SalesOrderLine.quantity - SalesOrderLine.fulfilled_quantity), 0)).join(SalesOrder, SalesOrder.id == SalesOrderLine.order_id).where(
        SalesOrder.company_id == company_id, SalesOrder.warehouse_id == warehouse_id, SalesOrderLine.product_id == product_id,
        SalesOrder.status.in_(OPEN_ORDER_STATES))
    if except_order_id is not None:
        q = q.where(SalesOrder.id != except_order_id)
    return db.scalar(q) or ZERO


def transition_order(db: Session, company_id: int, order_id: int, action: str, actor_id: int, ip: str | None):
    order = db.scalar(select(SalesOrder).where(SalesOrder.id == order_id, SalesOrder.company_id == company_id).options(selectinload(SalesOrder.lines)).with_for_update())
    if order is None:
        return None
    if action == "confirm" and order.status == "draft":
        customer = db.scalar(select(Customer).where(Customer.id == order.customer_id).with_for_update())
        grouped: dict[int, Decimal] = {}
        for line in order.lines:
            product = db.get(Product, line.product_id)
            if product.kind == "goods":
                grouped[line.product_id] = grouped.get(line.product_id, ZERO) + line.quantity
            else:
                line.fulfilled_quantity = line.quantity
        if all(line.fulfilled_quantity >= line.quantity for line in order.lines):
            order.status = "fulfilled"
        for product_id, quantity in sorted(grouped.items()):
            lock_stock_pool(db, company_id, product_id, order.warehouse_id)
            physical = db.scalar(select(func.coalesce(func.sum(InventoryBalance.quantity), 0)).where(
                InventoryBalance.company_id == company_id, InventoryBalance.product_id == product_id,
                InventoryBalance.warehouse_id == order.warehouse_id)) or ZERO
            if physical - _active_reserved(db, company_id, product_id, order.warehouse_id) < quantity:
                raise SalesError("Insufficient available stock for order reservation")
        if customer.credit_limit is not None:
            exposure = db.scalar(select(func.coalesce(func.sum(SalesInvoice.total - SalesInvoice.paid_amount - SalesInvoice.credited_amount), 0)).where(
                SalesInvoice.company_id == company_id, SalesInvoice.customer_id == customer.id, SalesInvoice.status.in_(("unpaid", "partial")))) or ZERO
            invoiced_for_order = select(func.coalesce(func.sum(SalesInvoice.total), 0)).where(
                SalesInvoice.company_id == company_id, SalesInvoice.order_id == SalesOrder.id,
                SalesInvoice.status != "void").scalar_subquery()
            open_commitments = db.scalar(select(func.coalesce(func.sum(SalesOrder.total - invoiced_for_order), 0)).where(
                SalesOrder.company_id == company_id, SalesOrder.customer_id == customer.id,
                SalesOrder.status.in_(("confirmed", "partially_fulfilled", "fulfilled")))) or ZERO
            if exposure + open_commitments + order.total > customer.credit_limit:
                raise SalesError("Customer credit limit would be exceeded")
        order.status = "confirmed"
    elif action == "cancel" and order.status in OPEN_ORDER_STATES:
        order.status = "cancelled"
    else:
        raise SalesError("Invalid sales order state transition")
    event = "sales_order_confirmed" if action == "confirm" else "sales_order_cancelled"
    _event(db, company_id, actor_id, event, ip, order_id=order.id, details={"order_id": order.id, "status": order.status})
    db.commit()
    return get_order(db, company_id, order.id)


def create_delivery(db: Session, company_id: int, order_id: int, data: DeliveryCreate, idem_key: str, actor_id: int, ip: str | None):
    _lock_idempotency(db, company_id, idem_key)
    order = db.scalar(select(SalesOrder).where(SalesOrder.id == order_id, SalesOrder.company_id == company_id).options(selectinload(SalesOrder.lines)).with_for_update())
    if order is None:
        raise SalesError("Sales order not found")
    digest = _hash({"order_id": order_id, "payload": data.model_dump(mode="json")})
    previous = db.scalar(select(SalesDelivery).where(SalesDelivery.company_id == company_id, SalesDelivery.idempotency_key == idem_key).options(selectinload(SalesDelivery.lines)))
    if previous:
        if previous.request_hash != digest:
            raise SalesError("Idempotency key already used for another request")
        return previous
    if order.status not in OPEN_ORDER_STATES:
        raise SalesError("Only confirmed orders can be delivered")
    requested = {entry.order_line_id: entry for entry in data.lines}
    if len(requested) != len(data.lines):
        raise SalesError("Delivery cannot repeat an order line")
    lines = {line.id: line for line in order.lines}
    for line_id, entry in sorted(requested.items(), key=lambda pair: (lines[pair[0]].product_id, pair[1].location_id or 0)):
        line = lines.get(line_id)
        if line is None or entry.quantity > line.quantity - line.fulfilled_quantity:
            raise SalesError("Delivery line is outside the order or exceeds its remaining quantity")
        product = db.get(Product, line.product_id)
        if product.kind != "goods":
            raise SalesError("Services are fulfilled on order confirmation and do not use physical delivery")
        if not product.allows_fractional_quantity and entry.quantity != entry.quantity.to_integral_value():
            raise SalesError("Product does not allow fractional quantities")
        if entry.location_id is not None and db.scalar(select(InventoryLocation.id).where(
            InventoryLocation.id == entry.location_id, InventoryLocation.company_id == company_id,
            InventoryLocation.warehouse_id == order.warehouse_id, InventoryLocation.is_active.is_(True))) is None:
            raise SalesError("Delivery location not found in order warehouse")
    delivery = SalesDelivery(company_id=company_id, order_id=order.id, warehouse_id=order.warehouse_id,
        idempotency_key=idem_key, request_hash=digest, notes=data.notes, actor_user_id=actor_id)
    db.add(delivery)
    db.flush()
    for line_id, entry in requested.items():
        line = lines[line_id]
        product = db.get(Product, line.product_id)
        operation = post_operation(db, company_id, InventoryOperationRequest(
            kind="issue", product_id=product.id, warehouse_id=order.warehouse_id, location_id=entry.location_id,
            quantity=entry.quantity, reason=f"Sales delivery {order.number}", reference_type="sales_delivery", reference_id=str(delivery.id)),
            f"sales-delivery:{delivery.id}:{line.id}", actor_id, ip, commit=False, reservation_order_id=order.id)
        line.fulfilled_quantity += entry.quantity
        db.add(SalesDeliveryLine(delivery_id=delivery.id, order_line_id=line.id, quantity=entry.quantity, inventory_operation_id=operation.id))
    if all(line.fulfilled_quantity >= line.quantity for line in order.lines):
        order.status = "fulfilled"
    else:
        order.status = "partially_fulfilled"
    _event(db, company_id, actor_id, "sales_delivery_posted", ip, order_id=order.id, details={
        "delivery_id": delivery.id, "order_id": order.id,
        "lines": [{"order_line_id": line_id, "quantity": str(entry.quantity)} for line_id, entry in requested.items()],
    })
    db.commit()
    return get_delivery(db, company_id, delivery.id)


def _lock_idempotency(db: Session, company_id: int, key: str):
    if not key or len(key) > 120:
        raise SalesError("A valid Idempotency-Key header is required")
    if db.get_bind().dialect.name == "postgresql":
        lock = int.from_bytes(hashlib.sha256(f"sales:{company_id}:{key}".encode()).digest()[:8], "big", signed=True)
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})


def get_delivery(db: Session, company_id: int, delivery_id: int):
    return db.scalar(select(SalesDelivery).where(SalesDelivery.company_id == company_id, SalesDelivery.id == delivery_id).options(selectinload(SalesDelivery.lines)))


def create_return(db: Session, company_id: int, data: ReturnCreate, idem_key: str, actor_id: int, ip: str | None):
    _lock_idempotency(db, company_id, idem_key)
    digest = _hash(data.model_dump(mode="json"))
    previous = db.scalar(select(SalesReturn).where(SalesReturn.company_id == company_id, SalesReturn.idempotency_key == idem_key))
    if previous:
        if previous.request_hash != digest:
            raise SalesError("Idempotency key already used for another request")
        return previous
    delivery_line = db.scalar(select(SalesDeliveryLine).join(SalesDelivery).where(
        SalesDeliveryLine.id == data.delivery_line_id, SalesDelivery.company_id == company_id).with_for_update())
    if delivery_line is None:
        raise SalesError("Sales delivery line not found")
    order_line = db.scalar(select(SalesOrderLine).join(SalesOrder).where(SalesOrderLine.id == delivery_line.order_line_id).with_for_update())
    order = db.get(SalesOrder, order_line.order_id)
    returned = db.scalar(select(func.coalesce(func.sum(SalesReturn.quantity), 0)).join(
        SalesDeliveryLine, SalesDeliveryLine.id == SalesReturn.delivery_line_id).where(
        SalesReturn.company_id == company_id, SalesReturn.delivery_line_id == delivery_line.id)) or ZERO
    if data.quantity > delivery_line.quantity - returned:
        raise SalesError("Return exceeds delivered quantity not previously returned")
    product = db.get(Product, order_line.product_id)
    operation = None
    if product.kind == "goods":
        operation = post_operation(db, company_id, InventoryOperationRequest(
            kind="receipt", product_id=product.id, warehouse_id=order.warehouse_id, quantity=data.quantity,
            reason=data.reason, reference_type="sales_return", reference_id=idem_key),
            f"sales-return:{company_id}:{idem_key}", actor_id, ip, commit=False)
    sales_return = SalesReturn(company_id=company_id, delivery_line_id=delivery_line.id, idempotency_key=idem_key,
        request_hash=digest, quantity=data.quantity, reason=data.reason,
        inventory_operation_id=operation.id, actor_user_id=actor_id)
    db.add(sales_return)
    db.flush()
    credit_details = None
    if data.invoice_id is not None:
        invoice = db.scalar(select(SalesInvoice).where(SalesInvoice.id == data.invoice_id, SalesInvoice.company_id == company_id).with_for_update())
        if invoice is None or invoice.order_id != order.id:
            raise SalesError("Invoice not found for this order")
        invoice_line = db.scalar(select(SalesInvoiceLine).where(SalesInvoiceLine.invoice_id == invoice.id, SalesInvoiceLine.order_line_id == order_line.id))
        if invoice_line is None:
            raise SalesError("Returned item is not on the selected invoice")
        credited_qty = db.scalar(select(func.coalesce(func.sum(SalesCreditNote.quantity), 0)).where(
            SalesCreditNote.invoice_id == invoice.id, SalesCreditNote.order_line_id == order_line.id)) or ZERO
        if data.quantity > invoice_line.quantity - credited_qty:
            raise SalesError("Return exceeds invoiced quantity available for credit")
        base = _money(data.quantity * invoice_line.unit_price * (Decimal("1") - invoice_line.discount_percent / Decimal("100")))
        credit_amount = base + _money(base * invoice_line.tax_rate / Decimal("100"))
        if credit_amount > invoice.total - invoice.paid_amount - invoice.credited_amount:
            raise SalesError("Credit note exceeds invoice balance due")
        note_number = f"CN-{sales_return.id}"
        db.add(SalesCreditNote(company_id=company_id, invoice_id=invoice.id, return_id=sales_return.id,
            order_line_id=order_line.id, quantity=data.quantity, number=note_number, amount=credit_amount,
            reason=data.reason, actor_user_id=actor_id))
        invoice.credited_amount += credit_amount
        invoice.status = "paid" if invoice.total - invoice.credited_amount == invoice.paid_amount else "partial"
        credit_details = {"credit_note_number": note_number, "credit_amount": str(credit_amount)}
    _event(db, company_id, actor_id, "sales_return_posted", ip, order_id=order.id,
        details={"return_id": sales_return.id, "quantity": str(data.quantity), **(credit_details or {})})
    db.commit()
    return sales_return


def create_invoice(db: Session, company_id: int, data: InvoiceCreate, actor_id: int, ip: str | None):
    order = db.scalar(select(SalesOrder).where(SalesOrder.id == data.order_id, SalesOrder.company_id == company_id).options(selectinload(SalesOrder.lines)).with_for_update())
    if order is None:
        raise SalesError("Sales order not found")
    if order.status not in ("partially_fulfilled", "fulfilled", "cancelled"):
        raise SalesError("Only fulfilled quantities can be invoiced")
    lines, subtotal, tax = [], ZERO, ZERO
    for order_line in order.lines:
        billed = db.scalar(select(func.coalesce(func.sum(SalesInvoiceLine.quantity), 0)).join(
            SalesInvoice, SalesInvoice.id == SalesInvoiceLine.invoice_id).where(
            SalesInvoice.company_id == company_id, SalesInvoice.order_id == order.id,
            SalesInvoice.status != "void", SalesInvoiceLine.order_line_id == order_line.id)) or ZERO
        quantity = order_line.fulfilled_quantity - billed
        if quantity <= 0:
            continue
        sub = _money(quantity * order_line.unit_price * (Decimal("1") - order_line.discount_percent / Decimal("100")))
        tax_amt = _money(sub * order_line.tax_rate / Decimal("100"))
        lines.append(SalesInvoiceLine(order_line_id=order_line.id, description=order_line.description, quantity=quantity,
            unit_price=order_line.unit_price, discount_percent=order_line.discount_percent, tax_rate=order_line.tax_rate,
            subtotal=sub, tax_amount=tax_amt, total=sub + tax_amt))
        subtotal += sub
        tax += tax_amt
    if not lines:
        raise SalesError("No fulfilled unbilled quantities remain")
    invoice = SalesInvoice(company_id=company_id, customer_id=order.customer_id, order_id=order.id,
        number=data.number, issued_date=data.issued_date, due_date=data.due_date, currency_code=order.currency_code,
        subtotal=subtotal, tax_total=tax, total=subtotal + tax, paid_amount=ZERO, credited_amount=ZERO,
        status="paid" if subtotal + tax == ZERO else "unpaid", notes=data.notes, actor_user_id=actor_id, lines=lines)
    db.add(invoice)
    db.flush()
    _event(db, company_id, actor_id, "sales_invoice_created", ip, order_id=order.id, details={"invoice_id": invoice.id, "number": invoice.number, "total": str(invoice.total)})
    db.commit()
    return get_invoice(db, company_id, invoice.id)


def get_invoice(db: Session, company_id: int, invoice_id: int):
    return db.scalar(select(SalesInvoice).where(SalesInvoice.id == invoice_id, SalesInvoice.company_id == company_id).options(selectinload(SalesInvoice.lines)))


def list_invoices(db: Session, company_id: int, limit: int, offset: int, customer_id: int | None):
    q = select(SalesInvoice).where(SalesInvoice.company_id == company_id).options(selectinload(SalesInvoice.lines))
    if customer_id:
        q = q.where(SalesInvoice.customer_id == customer_id)
    return list(db.scalars(q.order_by(SalesInvoice.due_date, SalesInvoice.id).offset(offset).limit(limit)))


def void_invoice(db: Session, company_id: int, invoice_id: int, actor_id: int, ip: str | None):
    invoice = db.scalar(select(SalesInvoice).where(SalesInvoice.id == invoice_id, SalesInvoice.company_id == company_id).with_for_update())
    if invoice is None:
        return None
    if invoice.status != "unpaid" or invoice.paid_amount != ZERO or invoice.credited_amount != ZERO:
        raise SalesError("Only unpaid invoices without payments or credit notes can be voided")
    invoice.status = "void"
    _audit(db, company_id, actor_id, "sales_invoice_voided", ip, {"invoice_id": invoice.id, "number": invoice.number})
    db.commit()
    return get_invoice(db, company_id, invoice.id)


def list_availability(db: Session, company_id: int, warehouse_id: int | None, product_id: int | None, limit: int, offset: int):
    q = select(InventoryBalance.product_id, InventoryBalance.warehouse_id,
        func.sum(InventoryBalance.quantity).label("physical_quantity")).where(InventoryBalance.company_id == company_id)
    if warehouse_id is not None:
        q = q.where(InventoryBalance.warehouse_id == warehouse_id)
    if product_id is not None:
        q = q.where(InventoryBalance.product_id == product_id)
    rows = db.execute(q.group_by(InventoryBalance.product_id, InventoryBalance.warehouse_id)
        .order_by(InventoryBalance.warehouse_id, InventoryBalance.product_id).offset(offset).limit(limit)).all()
    result = []
    for row in rows:
        reserved = _active_reserved(db, company_id, row.product_id, row.warehouse_id)
        result.append({"product_id": row.product_id, "warehouse_id": row.warehouse_id,
            "physical_quantity": row.physical_quantity, "reserved_quantity": reserved,
            "available_quantity": row.physical_quantity - reserved})
    return result


def post_payment(db: Session, company_id: int, invoice_id: int, data: PaymentCreate, idem_key: str, actor_id: int, ip: str | None):
    _lock_idempotency(db, company_id, idem_key)
    digest = _hash({"invoice_id": invoice_id, "payload": data.model_dump(mode="json")})
    previous = db.scalar(select(SalesPayment).where(SalesPayment.company_id == company_id, SalesPayment.idempotency_key == idem_key))
    if previous:
        if previous.request_hash != digest:
            raise SalesError("Idempotency key already used for another request")
        return previous
    invoice = db.scalar(select(SalesInvoice).where(SalesInvoice.id == invoice_id, SalesInvoice.company_id == company_id).with_for_update())
    if invoice is None:
        raise SalesError("Sales invoice not found")
    balance = invoice.total - invoice.paid_amount - invoice.credited_amount
    if invoice.status == "void" or data.amount > balance:
        raise SalesError("Payment exceeds invoice balance or invoice is void")
    invoice.paid_amount += data.amount
    invoice.status = "paid" if invoice.paid_amount + invoice.credited_amount == invoice.total else "partial"
    payment = SalesPayment(company_id=company_id, invoice_id=invoice.id, idempotency_key=idem_key,
        request_hash=digest, amount=data.amount, paid_on=data.paid_on, reference=data.reference, actor_user_id=actor_id)
    db.add(payment)
    db.flush()
    _audit(db, company_id, actor_id, "sales_payment_posted", ip, {"invoice_id": invoice.id, "payment_id": payment.id, "amount": str(data.amount), "balance_due": str(balance - data.amount)})
    db.commit()
    db.refresh(payment)
    return payment


