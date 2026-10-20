"""Business rules for suppliers, purchase orders, receipts, returns and payables."""

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, joinedload, selectinload

from backend.app.modules.catalog.models import Product
from backend.app.modules.companies.models import Branch, Company
from backend.app.modules.identity.models import SecurityEvent
from backend.app.modules.inventory.models import InventoryLocation, InventoryOperation, Warehouse
from backend.app.modules.inventory.schemas import InventoryOperationRequest
from backend.app.modules.inventory.service import InventoryError, post_operation as post_inventory_operation
from backend.app.modules.purchasing.models import (
    PurchaseOrder, PurchaseOrderEvent, PurchaseOrderLine, PurchaseReceipt, PurchaseReceiptLine,
    Supplier, SupplierInvoice, SupplierInvoiceLine, SupplierPayment, SupplierReturn, SupplierReturnLine,
)
from backend.app.modules.purchasing.schemas import (
    InvoiceLineInput, PurchaseOrderCreate, PurchaseOrderLineInput, PurchaseOrderUpdate, ReceiptCreate,
    SupplierCreate, SupplierInvoiceCreate, SupplierReturnCreate, SupplierUpdate, PaymentCreate,
)


CENT = Decimal("0.01")
ZERO = Decimal("0")


class PurchasingError(Exception):
    """Expected purchasing domain error safe to expose to API clients."""


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _hash(data: object) -> str:
    def normalize(value: object):
        if hasattr(value, "model_dump"):
            return normalize(value.model_dump(mode="json"))
        if isinstance(value, dict):
            return {key: normalize(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [normalize(item) for item in value]
        return value

    data = normalize(data)
    raw = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _security_event(session: Session, company_id: int, actor_id: int, event: str, source_ip: str | None, details: dict[str, object]) -> None:
    session.add(SecurityEvent(user_id=actor_id, company_id=company_id, event_type=event, source_ip=source_ip, details=details))


def _order_event(session: Session, order: PurchaseOrder, actor_id: int, event: str, details: dict[str, object]) -> None:
    session.add(PurchaseOrderEvent(company_id=order.company_id, purchase_order_id=order.id, actor_user_id=actor_id, event_type=event, details=details))


def list_suppliers(session: Session, company_id: int, limit: int, offset: int, search: str | None = None, active: bool | None = None) -> list[Supplier]:
    statement = select(Supplier).where(Supplier.company_id == company_id)
    if active is not None:
        statement = statement.where(Supplier.is_active.is_(active))
    if search:
        pattern = f"%{search.strip()}%"
        statement = statement.where(Supplier.legal_name.ilike(pattern) | Supplier.code.ilike(pattern) | Supplier.email.ilike(pattern))
    return list(session.scalars(statement.order_by(Supplier.legal_name, Supplier.id).offset(offset).limit(limit)))


def get_supplier(session: Session, company_id: int, supplier_id: int) -> Supplier | None:
    return session.scalar(select(Supplier).where(Supplier.company_id == company_id, Supplier.id == supplier_id))


def create_supplier(session: Session, company_id: int, data: SupplierCreate, actor_id: int, source_ip: str | None) -> Supplier:
    supplier = Supplier(company_id=company_id, **data.model_dump(exclude={"code"}, exclude_none=False), code=data.code.upper())
    session.add(supplier)
    session.flush()
    _security_event(session, company_id, actor_id, "supplier_created", source_ip, {"supplier_id": supplier.id, "code": supplier.code})
    session.commit()
    session.refresh(supplier)
    return supplier


def update_supplier(session: Session, company_id: int, supplier_id: int, data: SupplierUpdate, actor_id: int, source_ip: str | None) -> Supplier | None:
    supplier = session.scalar(select(Supplier).where(Supplier.id == supplier_id, Supplier.company_id == company_id).with_for_update())
    if supplier is None:
        return None
    changes = data.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(supplier, key, value)
    _security_event(session, company_id, actor_id, "supplier_updated", source_ip, {"supplier_id": supplier_id, "changed_fields": sorted(changes)})
    session.commit()
    session.refresh(supplier)
    return supplier


def _validate_order_scope(session: Session, company_id: int, supplier_id: int, branch_id: int | None, warehouse_id: int) -> Supplier:
    supplier = session.scalar(select(Supplier).where(Supplier.id == supplier_id, Supplier.company_id == company_id, Supplier.is_active.is_(True)))
    if supplier is None:
        raise PurchasingError("Active supplier not found in company")
    warehouse = session.scalar(select(Warehouse).where(Warehouse.id == warehouse_id, Warehouse.company_id == company_id, Warehouse.is_active.is_(True)))
    if warehouse is None:
        raise PurchasingError("Active destination warehouse not found in company")
    if branch_id is not None and session.scalar(select(Branch.id).where(Branch.id == branch_id, Branch.company_id == company_id, Branch.is_active.is_(True))) is None:
        raise PurchasingError("Branch not found in company")
    if warehouse.branch_id is not None and warehouse.branch_id != branch_id:
        raise PurchasingError("Destination warehouse does not belong to the selected branch")
    return supplier


def _make_order_lines(session: Session, company_id: int, items: list[PurchaseOrderLineInput]) -> tuple[list[PurchaseOrderLine], Decimal, Decimal, Decimal]:
    lines: list[PurchaseOrderLine] = []
    subtotal_total = ZERO
    tax_total = ZERO
    total = ZERO
    for item in items:
        product = session.scalar(select(Product).where(Product.id == item.product_id, Product.company_id == company_id, Product.is_active.is_(True)))
        if product is None:
            raise PurchasingError("Active product not found in company")
        if product.kind == "service" and item.quantity != item.quantity.to_integral_value():
            # Service units are still decimal where product settings explicitly allow it.
            if not product.allows_fractional_quantity:
                raise PurchasingError("Product does not allow fractional quantities")
        elif not product.allows_fractional_quantity and item.quantity != item.quantity.to_integral_value():
            raise PurchasingError("Product does not allow fractional quantities")
        raw = item.quantity * item.unit_price
        line_subtotal = _money(raw * (Decimal("1") - item.discount_percent / Decimal("100")))
        line_tax = _money(line_subtotal * item.tax_rate / Decimal("100"))
        line_total = line_subtotal + line_tax
        description = (item.description or product.name).strip()
        if not description:
            raise PurchasingError("Order line description cannot be blank")
        lines.append(PurchaseOrderLine(
            product_id=product.id, description=description, quantity=item.quantity,
            received_quantity=ZERO, unit_price=item.unit_price, discount_percent=item.discount_percent,
            tax_rate=item.tax_rate, subtotal=line_subtotal, tax_amount=line_tax, total=line_total,
        ))
        subtotal_total += line_subtotal
        tax_total += line_tax
        total += line_total
    return lines, subtotal_total, tax_total, total


def create_purchase_order(session: Session, company_id: int, data: PurchaseOrderCreate, actor_id: int, source_ip: str | None) -> PurchaseOrder:
    _validate_order_scope(session, company_id, data.supplier_id, data.branch_id, data.warehouse_id)
    lines, subtotal, tax_total, total = _make_order_lines(session, company_id, data.lines)
    order = PurchaseOrder(
        company_id=company_id, supplier_id=data.supplier_id, branch_id=data.branch_id,
        warehouse_id=data.warehouse_id, number=data.number, currency_code=data.currency_code,
        notes=data.notes, subtotal=subtotal, tax_total=tax_total, total=total,
        created_by_user_id=actor_id, status="draft", lines=lines,
    )
    session.add(order)
    session.flush()
    _order_event(session, order, actor_id, "created", {"number": order.number, "total": str(order.total)})
    _security_event(session, company_id, actor_id, "purchase_order_created", source_ip, {"purchase_order_id": order.id, "number": order.number})
    session.commit()
    return get_purchase_order(session, company_id, order.id)


def get_purchase_order(session: Session, company_id: int, order_id: int) -> PurchaseOrder | None:
    return session.scalar(select(PurchaseOrder).where(
        PurchaseOrder.company_id == company_id, PurchaseOrder.id == order_id,
    ).options(selectinload(PurchaseOrder.lines)))


def list_purchase_orders(session: Session, company_id: int, limit: int, offset: int, status: str | None = None, supplier_id: int | None = None) -> list[PurchaseOrder]:
    statement = select(PurchaseOrder).where(PurchaseOrder.company_id == company_id)
    if status:
        statement = statement.where(PurchaseOrder.status == status)
    if supplier_id:
        statement = statement.where(PurchaseOrder.supplier_id == supplier_id)
    return list(session.scalars(statement.options(selectinload(PurchaseOrder.lines)).order_by(PurchaseOrder.id.desc()).offset(offset).limit(limit)))


def update_purchase_order(session: Session, company_id: int, order_id: int, data: PurchaseOrderUpdate, actor_id: int, source_ip: str | None) -> PurchaseOrder | None:
    order = session.scalar(select(PurchaseOrder).where(PurchaseOrder.id == order_id, PurchaseOrder.company_id == company_id).options(selectinload(PurchaseOrder.lines)).with_for_update())
    if order is None:
        return None
    if order.status != "draft":
        raise PurchasingError("Only draft purchase orders can be edited")
    changes = data.model_dump(exclude_unset=True)
    supplier_id = changes.get("supplier_id", order.supplier_id)
    branch_id = changes.get("branch_id", order.branch_id)
    warehouse_id = changes.get("warehouse_id", order.warehouse_id)
    _validate_order_scope(session, company_id, supplier_id, branch_id, warehouse_id)
    for key in ("supplier_id", "branch_id", "warehouse_id", "currency_code", "notes"):
        if key in changes:
            setattr(order, key, changes[key])
    if "lines" in changes:
        new_lines, _, _, _ = _make_order_lines(session, company_id, data.lines)
        order.lines = new_lines
    order.subtotal = sum((line.subtotal for line in order.lines), ZERO)
    order.tax_total = sum((line.tax_amount for line in order.lines), ZERO)
    order.total = sum((line.total for line in order.lines), ZERO)
    _order_event(session, order, actor_id, "updated", {"changed_fields": sorted(changes), "total": str(order.total)})
    _security_event(session, company_id, actor_id, "purchase_order_updated", source_ip, {"purchase_order_id": order.id, "changed_fields": sorted(changes)})
    session.commit()
    return get_purchase_order(session, company_id, order.id)


def transition_purchase_order(session: Session, company_id: int, order_id: int, action: str, actor_id: int, source_ip: str | None) -> PurchaseOrder | None:
    order = session.scalar(select(PurchaseOrder).where(PurchaseOrder.id == order_id, PurchaseOrder.company_id == company_id).options(selectinload(PurchaseOrder.lines)).with_for_update())
    if order is None:
        return None
    if action == "confirm":
        if order.status != "draft" or not order.lines:
            raise PurchasingError("Only a nonempty draft can be confirmed")
        _validate_order_scope(session, company_id, order.supplier_id, order.branch_id, order.warehouse_id)
        order.status = "confirmed"
        order.confirmed_by_user_id = actor_id
        order.confirmed_at = datetime.now(timezone.utc)
    elif action == "cancel":
        if order.status not in {"draft", "confirmed"}:
            raise PurchasingError("Only a draft or unreceived confirmed order can be cancelled")
        receipt_exists = session.scalar(select(PurchaseReceipt.id).where(PurchaseReceipt.company_id == company_id, PurchaseReceipt.purchase_order_id == order.id).limit(1))
        if receipt_exists:
            raise PurchasingError("An order with receipts cannot be cancelled")
        order.status = "cancelled"
        order.cancelled_by_user_id = actor_id
        order.cancelled_at = datetime.now(timezone.utc)
    elif action == "close":
        if order.status != "confirmed":
            raise PurchasingError("Only a confirmed order can be closed")
        outstanding = session.scalar(select(func.count(PurchaseOrderLine.id)).join(Product, Product.id == PurchaseOrderLine.product_id).where(
            PurchaseOrderLine.purchase_order_id == order.id, Product.kind == "goods",
            PurchaseOrderLine.received_quantity < PurchaseOrderLine.quantity,
        )) or 0
        if outstanding:
            raise PurchasingError("Order has outstanding goods")
        order.status = "closed"
    else:
        raise PurchasingError("Invalid order action")
    event_name = {"confirm": "confirmed", "cancel": "cancelled", "close": "closed"}[action]
    _order_event(session, order, actor_id, event_name, {"status": order.status})
    _security_event(session, company_id, actor_id, f"purchase_order_{event_name}", source_ip, {"purchase_order_id": order.id})
    session.commit()
    return get_purchase_order(session, company_id, order.id)


def _lock_idempotency(session: Session, company_id: int, key: str) -> None:
    if not key or len(key) > 120:
        raise PurchasingError("A valid Idempotency-Key header is required")
    if session.get_bind().dialect.name == "postgresql":
        lock = int.from_bytes(hashlib.sha256(f"purchasing:{company_id}:{key}".encode()).digest()[:8], "big", signed=True)
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})


def get_receipt(session: Session, company_id: int, receipt_id: int) -> PurchaseReceipt | None:
    return session.scalar(select(PurchaseReceipt).where(PurchaseReceipt.id == receipt_id, PurchaseReceipt.company_id == company_id).options(selectinload(PurchaseReceipt.lines)))


def receive_purchase_order(
    session: Session, company_id: int, order_id: int, data: ReceiptCreate, idempotency_key: str,
    actor_id: int, source_ip: str | None,
) -> PurchaseReceipt:
    _lock_idempotency(session, company_id, idempotency_key)
    order = session.scalar(select(PurchaseOrder).where(
        PurchaseOrder.id == order_id, PurchaseOrder.company_id == company_id,
    ).options(selectinload(PurchaseOrder.lines)).with_for_update())
    if order is None:
        raise PurchasingError("Purchase order not found")
    digest = _hash({"order_id": order_id, "payload": data})
    previous = session.scalar(select(PurchaseReceipt).where(PurchaseReceipt.company_id == company_id, PurchaseReceipt.idempotency_key == idempotency_key).options(selectinload(PurchaseReceipt.lines)))
    if previous is not None:
        if previous.request_hash != digest:
            raise PurchasingError("Idempotency key was already used for another request")
        return previous
    if order.status != "confirmed":
        raise PurchasingError("Only confirmed orders can receive goods")
    requested = {line.purchase_order_line_id: line for line in data.lines}
    if len(requested) != len(data.lines):
        raise PurchasingError("Receipt cannot repeat the same order line")
    order_lines = {line.id: line for line in order.lines}
    for line_id, item in requested.items():
        order_line = order_lines.get(line_id)
        if order_line is None:
            raise PurchasingError("Receipt line does not belong to this order")
        product = session.scalar(select(Product).where(Product.id == order_line.product_id, Product.company_id == company_id, Product.is_active.is_(True)))
        if product is None or product.kind != "goods":
            raise PurchasingError("Only active goods can be received into inventory")
        if not product.allows_fractional_quantity and (
            item.accepted_quantity != item.accepted_quantity.to_integral_value()
            or item.damaged_quantity != item.damaged_quantity.to_integral_value()
        ):
            raise PurchasingError("Product does not allow fractional quantities")
        if item.accepted_quantity + item.damaged_quantity > order_line.quantity - order_line.received_quantity:
            raise PurchasingError("Receipt exceeds the remaining quantity on the order")
        if item.location_id is not None and session.scalar(select(InventoryLocation.id).where(
            InventoryLocation.id == item.location_id, InventoryLocation.company_id == company_id,
            InventoryLocation.warehouse_id == order.warehouse_id, InventoryLocation.is_active.is_(True),
        )) is None:
            raise PurchasingError("Receipt location not found in destination warehouse")
    receipt = PurchaseReceipt(
        company_id=company_id, purchase_order_id=order.id, warehouse_id=order.warehouse_id,
        idempotency_key=idempotency_key, request_hash=digest, notes=data.notes, actor_user_id=actor_id,
    )
    session.add(receipt)
    session.flush()
    for item in sorted(data.lines, key=lambda entry: (order_lines[entry.purchase_order_line_id].product_id, entry.location_id or 0)):
        order_line = order_lines[item.purchase_order_line_id]
        receipt_line = PurchaseReceiptLine(
            receipt_id=receipt.id, purchase_order_line_id=order_line.id, product_id=order_line.product_id,
            location_id=item.location_id, accepted_quantity=item.accepted_quantity,
            damaged_quantity=item.damaged_quantity, notes=item.notes,
        )
        session.add(receipt_line)
        order_line.received_quantity += item.accepted_quantity + item.damaged_quantity
        if item.accepted_quantity > 0:
            operation = post_inventory_operation(
                session, company_id,
                InventoryOperationRequest(
                    kind="receipt", product_id=order_line.product_id, warehouse_id=order.warehouse_id,
                    location_id=item.location_id, quantity=item.accepted_quantity,
                    unit_cost=order_line.unit_price, reason=f"Purchase receipt {order.number}",
                    notes=item.notes, reference_type="purchase_receipt", reference_id=str(receipt.id),
                ),
                f"purchase-receipt:{receipt.id}:{order_line.id}", actor_id, source_ip, commit=False,
            )
            if operation.id is None:
                session.flush()
            receipt_line.inventory_operation_id = operation.id
    _order_event(session, order, actor_id, "received", {
        "receipt_id": receipt.id, "line_count": len(data.lines),
        "accepted_quantity": str(sum((line.accepted_quantity for line in data.lines), ZERO)),
        "damaged_quantity": str(sum((line.damaged_quantity for line in data.lines), ZERO)),
    })
    _security_event(session, company_id, actor_id, "purchase_receipt_posted", source_ip, {"receipt_id": receipt.id, "purchase_order_id": order.id})
    session.commit()
    return get_receipt(session, company_id, receipt.id)


def create_supplier_return(
    session: Session, company_id: int, receipt_id: int, data: SupplierReturnCreate, idempotency_key: str,
    actor_id: int, source_ip: str | None,
) -> SupplierReturn:
    _lock_idempotency(session, company_id, idempotency_key)
    receipt = session.scalar(select(PurchaseReceipt).where(
        PurchaseReceipt.id == receipt_id, PurchaseReceipt.company_id == company_id,
    ).options(selectinload(PurchaseReceipt.lines)).with_for_update())
    if receipt is None:
        raise PurchasingError("Purchase receipt not found")
    digest = _hash({"receipt_id": receipt_id, "payload": data})
    previous = session.scalar(select(SupplierReturn).where(SupplierReturn.company_id == company_id, SupplierReturn.idempotency_key == idempotency_key))
    if previous is not None:
        if previous.request_hash != digest:
            raise PurchasingError("Idempotency key was already used for another request")
        return previous
    requested = {line.receipt_line_id: line for line in data.lines}
    if len(requested) != len(data.lines):
        raise PurchasingError("Return cannot repeat the same receipt line")
    receipt_lines = {line.id: line for line in receipt.lines}
    for receipt_line_id, item in sorted(requested.items(), key=lambda pair: (receipt_lines[pair[0]].product_id, receipt_lines[pair[0]].location_id or 0)):
        receipt_line = receipt_lines.get(receipt_line_id)
        if receipt_line is None:
            raise PurchasingError("Return line does not belong to this receipt")
        already_returned = session.scalar(select(func.coalesce(func.sum(SupplierReturnLine.quantity), 0)).join(
            SupplierReturn, SupplierReturn.id == SupplierReturnLine.supplier_return_id,
        ).where(SupplierReturnLine.receipt_line_id == receipt_line_id, SupplierReturn.company_id == company_id)) or ZERO
        if item.quantity > receipt_line.accepted_quantity - already_returned:
            raise PurchasingError("Return exceeds the accepted quantity on the receipt")
    supplier_return = SupplierReturn(
        company_id=company_id, receipt_id=receipt.id, idempotency_key=idempotency_key,
        request_hash=digest, reason=data.reason, actor_user_id=actor_id,
    )
    session.add(supplier_return)
    session.flush()
    for receipt_line_id, item in requested.items():
        receipt_line = receipt_lines[receipt_line_id]
        order_line = session.get(PurchaseOrderLine, receipt_line.purchase_order_line_id)
        operation = post_inventory_operation(
            session, company_id,
            InventoryOperationRequest(
                kind="issue", product_id=receipt_line.product_id,
                warehouse_id=receipt.warehouse_id, location_id=receipt_line.location_id,
                quantity=item.quantity, unit_cost=order_line.unit_price, reason=data.reason,
                reference_type="supplier_return", reference_id=str(supplier_return.id),
            ),
            f"supplier-return:{supplier_return.id}:{receipt_line_id}", actor_id, source_ip, commit=False,
        )
        session.add(SupplierReturnLine(
            supplier_return_id=supplier_return.id, receipt_line_id=receipt_line_id,
            quantity=item.quantity, inventory_operation_id=operation.id,
        ))
    _security_event(session, company_id, actor_id, "supplier_return_posted", source_ip, {
        "supplier_return_id": supplier_return.id, "receipt_id": receipt.id, "reason": data.reason,
    })
    session.commit()
    return supplier_return


def _calculated_invoice_line(
    session: Session, company_id: int, supplier_id: int, item: InvoiceLineInput,
    order_id: int | None, receipt_id: int | None,
) -> SupplierInvoiceLine:
    po_line_id = item.purchase_order_line_id
    receipt_line_id = item.receipt_line_id
    if po_line_id is not None:
        po_line = session.scalar(select(PurchaseOrderLine).join(PurchaseOrder).where(
            PurchaseOrderLine.id == po_line_id, PurchaseOrder.company_id == company_id,
        ))
        if po_line is None:
            raise PurchasingError("Invoice order line is not in this company")
        po_header = session.get(PurchaseOrder, po_line.purchase_order_id)
        if po_header.supplier_id != supplier_id or (order_id is not None and po_line.purchase_order_id != order_id):
            raise PurchasingError("Invoice order line is not in this supplier or order")
    if receipt_line_id is not None:
        receipt_line = session.scalar(select(PurchaseReceiptLine).join(PurchaseReceipt).where(
            PurchaseReceiptLine.id == receipt_line_id, PurchaseReceipt.company_id == company_id,
        ))
        if receipt_line is None:
            raise PurchasingError("Invoice receipt line is not in this company")
        receipt_header = session.get(PurchaseReceipt, receipt_line.receipt_id)
        receipt_order = session.get(PurchaseOrder, receipt_header.purchase_order_id)
        if receipt_order.supplier_id != supplier_id or (receipt_id is not None and receipt_line.receipt_id != receipt_id):
            raise PurchasingError("Invoice receipt line is not in this supplier or receipt")
        if po_line_id is not None and receipt_line.purchase_order_line_id != po_line_id:
            raise PurchasingError("Invoice order and receipt lines do not correspond")
        if order_id is not None:
            header = session.get(PurchaseReceipt, receipt_line.receipt_id)
            if header.purchase_order_id != order_id:
                raise PurchasingError("Invoice receipt does not belong to the selected order")
    raw = item.quantity * item.unit_price
    subtotal = _money(raw)
    tax = _money(subtotal * item.tax_rate / Decimal("100"))
    return SupplierInvoiceLine(
        purchase_order_line_id=po_line_id, receipt_line_id=receipt_line_id,
        description=item.description.strip(), quantity=item.quantity, unit_price=item.unit_price,
        tax_rate=item.tax_rate, subtotal=subtotal, tax_amount=tax, total=subtotal + tax,
    )


def create_supplier_invoice(session: Session, company_id: int, data: SupplierInvoiceCreate, actor_id: int, source_ip: str | None) -> SupplierInvoice:
    supplier = session.scalar(select(Supplier).where(Supplier.id == data.supplier_id, Supplier.company_id == company_id))
    if supplier is None:
        raise PurchasingError("Supplier not found in company")
    if data.purchase_order_id is not None:
        order = session.scalar(select(PurchaseOrder).where(PurchaseOrder.id == data.purchase_order_id, PurchaseOrder.company_id == company_id))
        if order is None or order.supplier_id != supplier.id:
            raise PurchasingError("Purchase order does not belong to this supplier and company")
    if data.receipt_id is not None:
        receipt = session.scalar(select(PurchaseReceipt).where(PurchaseReceipt.id == data.receipt_id, PurchaseReceipt.company_id == company_id))
        if receipt is None:
            raise PurchasingError("Receipt not found in company")
        order = session.get(PurchaseOrder, receipt.purchase_order_id)
        if order.supplier_id != supplier.id or (data.purchase_order_id is not None and data.purchase_order_id != order.id):
            raise PurchasingError("Receipt does not belong to this supplier or order")
    lines = [_calculated_invoice_line(session, company_id, supplier.id, item, data.purchase_order_id, data.receipt_id) for item in data.lines]
    subtotal = sum((line.subtotal for line in lines), ZERO)
    tax_total = sum((line.tax_amount for line in lines), ZERO)
    total = sum((line.total for line in lines), ZERO)
    invoice = SupplierInvoice(
        company_id=company_id, supplier_id=supplier.id, purchase_order_id=data.purchase_order_id,
        receipt_id=data.receipt_id, document_number=data.document_number, issued_date=data.issued_date,
        due_date=data.due_date, currency_code=data.currency_code, subtotal=subtotal, tax_total=tax_total,
        total=total, paid_amount=ZERO, status="paid" if total == 0 else "unpaid",
        notes=data.notes, actor_user_id=actor_id, lines=lines,
    )
    session.add(invoice)
    session.flush()
    _security_event(session, company_id, actor_id, "supplier_invoice_created", source_ip, {
        "invoice_id": invoice.id, "supplier_id": supplier.id, "document_number": invoice.document_number,
        "total": str(total), "currency_code": invoice.currency_code,
    })
    session.commit()
    return invoice


def list_supplier_invoices(session: Session, company_id: int, limit: int, offset: int, supplier_id: int | None = None) -> list[SupplierInvoice]:
    statement = select(SupplierInvoice).where(SupplierInvoice.company_id == company_id)
    if supplier_id is not None:
        statement = statement.where(SupplierInvoice.supplier_id == supplier_id)
    return list(session.scalars(statement.options(selectinload(SupplierInvoice.lines)).order_by(SupplierInvoice.due_date, SupplierInvoice.id).offset(offset).limit(limit)))


def get_supplier_invoice(session: Session, company_id: int, invoice_id: int) -> SupplierInvoice | None:
    return session.scalar(select(SupplierInvoice).where(
        SupplierInvoice.company_id == company_id, SupplierInvoice.id == invoice_id,
    ).options(selectinload(SupplierInvoice.lines)))


def post_supplier_payment(
    session: Session, company_id: int, invoice_id: int, data: PaymentCreate, idempotency_key: str,
    actor_id: int, source_ip: str | None,
) -> SupplierPayment:
    _lock_idempotency(session, company_id, idempotency_key)
    digest = _hash({"invoice_id": invoice_id, "payload": data})
    previous = session.scalar(select(SupplierPayment).where(SupplierPayment.company_id == company_id, SupplierPayment.idempotency_key == idempotency_key))
    if previous is not None:
        if previous.request_hash != digest:
            raise PurchasingError("Idempotency key was already used for another request")
        return previous
    invoice = session.scalar(select(SupplierInvoice).where(
        SupplierInvoice.id == invoice_id, SupplierInvoice.company_id == company_id,
    ).with_for_update())
    if invoice is None:
        raise PurchasingError("Supplier invoice not found")
    if invoice.status == "void":
        raise PurchasingError("Void invoices cannot be paid")
    if data.amount > invoice.total - invoice.paid_amount:
        raise PurchasingError("Payment exceeds invoice balance due")
    invoice.paid_amount += data.amount
    invoice.status = "paid" if invoice.paid_amount == invoice.total else "partial"
    payment = SupplierPayment(
        company_id=company_id, invoice_id=invoice.id, idempotency_key=idempotency_key,
        request_hash=digest, amount=data.amount, paid_on=data.paid_on, reference=data.reference,
        actor_user_id=actor_id,
    )
    session.add(payment)
    session.flush()
    _security_event(session, company_id, actor_id, "supplier_payment_posted", source_ip, {
        "payment_id": payment.id, "invoice_id": invoice.id, "amount": str(data.amount),
        "paid_amount": str(invoice.paid_amount), "status": invoice.status,
    })
    session.commit()
    session.refresh(payment)
    return payment


def void_supplier_invoice(session: Session, company_id: int, invoice_id: int, actor_id: int, source_ip: str | None) -> SupplierInvoice | None:
    invoice = session.scalar(select(SupplierInvoice).where(SupplierInvoice.id == invoice_id, SupplierInvoice.company_id == company_id).with_for_update())
    if invoice is None:
        return None
    if invoice.status != "unpaid" or invoice.paid_amount != ZERO:
        raise PurchasingError("Only unpaid invoices without payments can be voided")
    invoice.status = "void"
    _security_event(session, company_id, actor_id, "supplier_invoice_voided", source_ip, {"invoice_id": invoice.id})
    session.commit()
    session.refresh(invoice)
    return invoice
