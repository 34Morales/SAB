"""Transactional inventory use cases and balance reconciliation."""

import hashlib
import json
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from backend.app.modules.catalog.models import Product
from backend.app.modules.companies.models import Branch, Company
from backend.app.modules.identity.models import SecurityEvent
from backend.app.modules.inventory.models import InventoryBalance, InventoryLocation, InventoryMovement, InventoryOperation, Warehouse
from backend.app.modules.inventory.schemas import InventoryOperationRequest, LocationCreate, WarehouseCreate


class InventoryError(Exception):
    """Expected domain error; the API maps it to a safe client response."""


def _lock_key(company_id: int, product_id: int, warehouse_id: int, location_id: int | None) -> int:
    raw = f"{company_id}:{product_id}:{warehouse_id}:{location_id or 0}".encode()
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big", signed=True)


def lock_stock_pool(session: Session, company_id: int, product_id: int, warehouse_id: int) -> None:
    """Serialize reservations and physical stock changes across all locations in a warehouse."""
    if session.get_bind().dialect.name == "postgresql":
        key = int.from_bytes(hashlib.sha256(f"stock-pool:{company_id}:{product_id}:{warehouse_id}".encode()).digest()[:8], "big", signed=True)
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


def _lock_position(session: Session, company_id: int, product_id: int, warehouse_id: int, location_id: int | None) -> InventoryBalance:
    dialect = session.get_bind().dialect.name
    lock_key = _lock_key(company_id, product_id, warehouse_id, location_id)
    if dialect == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    condition = (
        (InventoryBalance.company_id == company_id)
        & (InventoryBalance.product_id == product_id)
        & (InventoryBalance.warehouse_id == warehouse_id)
        & (InventoryBalance.location_id.is_(None) if location_id is None else InventoryBalance.location_id == location_id)
    )
    row = session.scalar(select(InventoryBalance).where(condition).with_for_update())
    if row is None:
        row = InventoryBalance(company_id=company_id, product_id=product_id, warehouse_id=warehouse_id, location_id=location_id)
        session.add(row)
        session.flush()
    return row


def create_warehouse(session: Session, company_id: int, data: WarehouseCreate, actor_id: int, source_ip: str | None) -> Warehouse:
    if data.branch_id is not None and session.scalar(select(Branch.id).where(
        Branch.id == data.branch_id, Branch.company_id == company_id, Branch.is_active.is_(True)
    )) is None:
        raise InventoryError("Branch not found in active company")
    warehouse = Warehouse(company_id=company_id, code=data.code.strip().upper(), name=data.name.strip(), branch_id=data.branch_id)
    session.add(warehouse)
    session.flush()
    session.add(SecurityEvent(user_id=actor_id, company_id=company_id, event_type="inventory_warehouse_created", source_ip=source_ip, details={"warehouse_id": warehouse.id}))
    session.commit()
    session.refresh(warehouse)
    return warehouse


def list_warehouses(session: Session, company_id: int) -> list[Warehouse]:
    return list(session.scalars(select(Warehouse).where(Warehouse.company_id == company_id).order_by(Warehouse.id)))


def create_location(session: Session, company_id: int, data: LocationCreate, actor_id: int, source_ip: str | None) -> InventoryLocation:
    warehouse = session.scalar(select(Warehouse).where(Warehouse.id == data.warehouse_id, Warehouse.company_id == company_id, Warehouse.is_active.is_(True)))
    if warehouse is None:
        raise InventoryError("Active warehouse not found in company")
    location = InventoryLocation(company_id=company_id, warehouse_id=warehouse.id, code=data.code.strip().upper(), name=data.name.strip())
    session.add(location)
    session.flush()
    session.add(SecurityEvent(user_id=actor_id, company_id=company_id, event_type="inventory_location_created", source_ip=source_ip, details={"location_id": location.id, "warehouse_id": warehouse.id}))
    session.commit()
    session.refresh(location)
    return location


def list_locations(session: Session, company_id: int, warehouse_id: int) -> list[InventoryLocation]:
    return list(session.scalars(select(InventoryLocation).where(InventoryLocation.company_id == company_id, InventoryLocation.warehouse_id == warehouse_id).order_by(InventoryLocation.id)))


def deactivate_warehouse(session: Session, company_id: int, warehouse_id: int, actor_id: int, source_ip: str | None) -> Warehouse | None:
    warehouse = session.scalar(select(Warehouse).where(Warehouse.id == warehouse_id, Warehouse.company_id == company_id).with_for_update())
    if warehouse is None:
        return None
    if warehouse.is_active is False:
        return warehouse
    if session.scalar(select(InventoryBalance.id).where(
        InventoryBalance.company_id == company_id, InventoryBalance.warehouse_id == warehouse_id, InventoryBalance.quantity != 0
    ).limit(1)) is not None:
        raise InventoryError("Warehouse has nonzero stock")
    warehouse.is_active = False
    session.add(SecurityEvent(user_id=actor_id, company_id=company_id, event_type="inventory_warehouse_deactivated", source_ip=source_ip, details={"warehouse_id": warehouse_id}))
    session.commit()
    session.refresh(warehouse)
    return warehouse


def post_operation(
    session: Session, company_id: int, data: InventoryOperationRequest, idempotency_key: str,
    actor_id: int, source_ip: str | None, commit: bool = True,
    reservation_order_id: int | None = None,
) -> InventoryOperation:
    if not idempotency_key or len(idempotency_key) > 120:
        raise InventoryError("A valid Idempotency-Key header is required")
    if session.get_bind().dialect.name == "postgresql":
        idem_key = int.from_bytes(hashlib.sha256(f"idem:{company_id}:{idempotency_key}".encode()).digest()[:8], "big", signed=True)
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": idem_key})
    payload = data.model_dump(mode="json")
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    existing = session.scalar(select(InventoryOperation).where(
        InventoryOperation.company_id == company_id, InventoryOperation.idempotency_key == idempotency_key
    ))
    if existing:
        if existing.request_hash != digest:
            raise InventoryError("Idempotency key was already used for another request")
        return existing
    product = session.scalar(select(Product).where(Product.id == data.product_id, Product.company_id == company_id, Product.is_active.is_(True)))
    if product is None or product.kind != "goods":
        raise InventoryError("Active inventory product not found")
    if not product.allows_fractional_quantity and data.quantity != data.quantity.to_integral_value():
        raise InventoryError("Product does not allow fractional quantities")
    company = session.get(Company, company_id)
    if company is None:
        raise InventoryError("Company not found")
    source = session.scalar(select(Warehouse).where(Warehouse.id == data.warehouse_id, Warehouse.company_id == company_id, Warehouse.is_active.is_(True)))
    if source is None:
        raise InventoryError("Active warehouse not found in company")
    destination = None
    if data.destination_warehouse_id is not None:
        destination = session.scalar(select(Warehouse).where(Warehouse.id == data.destination_warehouse_id, Warehouse.company_id == company_id, Warehouse.is_active.is_(True)))
        if destination is None:
            raise InventoryError("Destination warehouse not found in company")
    for location_id, warehouse_id in ((data.location_id, data.warehouse_id), (data.destination_location_id, data.destination_warehouse_id)):
        if location_id is not None and session.scalar(select(InventoryLocation.id).where(
            InventoryLocation.id == location_id, InventoryLocation.company_id == company_id,
            InventoryLocation.warehouse_id == warehouse_id, InventoryLocation.is_active.is_(True),
        )) is None:
            raise InventoryError("Location not found in warehouse")

    company_allows_negative = bool(getattr(company, "allow_negative_stock", False))
    positions = [(data.warehouse_id, data.location_id, -1 if data.kind in {"issue", "adjustment_negative", "transfer"} else 1)]
    if destination:
        positions.append((destination.id, data.destination_location_id, 1))
    outgoing = [(warehouse_id, location_id) for warehouse_id, location_id, direction in positions if direction < 0]
    for warehouse_id, _location_id in sorted(outgoing):
        lock_stock_pool(session, company_id, product.id, warehouse_id)
    if outgoing:
        from backend.app.modules.sales.models import SalesOrder, SalesOrderLine

        physical = session.scalar(select(func.coalesce(func.sum(InventoryBalance.quantity), 0)).where(
            InventoryBalance.company_id == company_id, InventoryBalance.product_id == product.id,
            InventoryBalance.warehouse_id == data.warehouse_id,
        )) or Decimal("0")
        reserved_query = select(func.coalesce(func.sum(SalesOrderLine.quantity - SalesOrderLine.fulfilled_quantity), 0)).join(
            SalesOrder, SalesOrder.id == SalesOrderLine.order_id,
        ).where(SalesOrder.company_id == company_id, SalesOrder.warehouse_id == data.warehouse_id,
                SalesOrderLine.product_id == product.id, SalesOrder.status.in_(("confirmed", "partially_fulfilled")))
        if reservation_order_id is not None:
            reserved_query = reserved_query.where(SalesOrder.id != reservation_order_id)
        reserved = session.scalar(reserved_query) or Decimal("0")
        if physical - reserved < data.quantity and (reserved > 0 or not company_allows_negative):
                raise InventoryError("Insufficient stock: sales reservations reduce available quantity")
    # Stable ordering prevents deadlocks when concurrent transfers overlap.
    balances = {}
    for warehouse_id, location_id, _ in sorted(positions, key=lambda item: (item[0], item[1] or 0)):
        balances[(warehouse_id, location_id)] = _lock_position(session, company_id, product.id, warehouse_id, location_id)
    for warehouse_id, location_id, direction in positions:
        balance = balances[(warehouse_id, location_id)]
        if direction < 0 and not company_allows_negative and balance.quantity < data.quantity:
            raise InventoryError("Insufficient stock")
    operation = InventoryOperation(
        company_id=company_id, kind=data.kind, idempotency_key=idempotency_key, request_hash=digest,
        reference_type=data.reference_type, reference_id=data.reference_id, reason=data.reason,
        notes=data.notes, actor_user_id=actor_id,
    )
    session.add(operation)
    session.flush()
    movement_type = data.kind
    for warehouse_id, location_id, direction in positions:
        balances[(warehouse_id, location_id)].quantity += data.quantity * direction
        session.add(InventoryMovement(
            company_id=company_id, operation_id=operation.id, product_id=product.id,
            warehouse_id=warehouse_id, location_id=location_id, movement_type=movement_type,
            direction=direction, quantity=data.quantity, unit_cost=data.unit_cost,
        ))
    session.add(SecurityEvent(
        user_id=actor_id, company_id=company_id, event_type="inventory_operation_posted", source_ip=source_ip,
        details={"operation_id": operation.id, "kind": data.kind, "product_id": product.id, "quantity": str(data.quantity), "reason": data.reason},
    ))
    if commit:
        session.commit()
        session.refresh(operation)
    return operation


def reconcile_balances(session: Session, company_id: int, repair: bool = False) -> list[dict[str, object]]:
    """Compare cached balances to signed ledger totals; optionally repair the projection."""
    from sqlalchemy import case, func

    ledger = session.execute(select(
        InventoryMovement.product_id, InventoryMovement.warehouse_id, InventoryMovement.location_id,
        func.sum(InventoryMovement.quantity * InventoryMovement.direction).label("quantity"),
    ).where(InventoryMovement.company_id == company_id).group_by(
        InventoryMovement.product_id, InventoryMovement.warehouse_id, InventoryMovement.location_id,
    )).all()
    mismatches = []
    ledger_positions = set()
    for product_id, warehouse_id, location_id, expected in ledger:
        ledger_positions.add((product_id, warehouse_id, location_id))
        balance = session.scalar(select(InventoryBalance).where(
            InventoryBalance.company_id == company_id, InventoryBalance.product_id == product_id,
            InventoryBalance.warehouse_id == warehouse_id,
            InventoryBalance.location_id.is_(None) if location_id is None else InventoryBalance.location_id == location_id,
        ).with_for_update())
        actual = balance.quantity if balance else Decimal("0")
        if actual != expected:
            mismatches.append({"product_id": product_id, "warehouse_id": warehouse_id, "location_id": location_id, "expected": expected, "actual": actual})
            if repair:
                if balance is None:
                    balance = InventoryBalance(company_id=company_id, product_id=product_id, warehouse_id=warehouse_id, location_id=location_id)
                    session.add(balance)
                balance.quantity = expected
    for balance in session.scalars(select(InventoryBalance).where(InventoryBalance.company_id == company_id)).all():
        key = (balance.product_id, balance.warehouse_id, balance.location_id)
        if key in ledger_positions or balance.quantity == 0:
            continue
        mismatches.append({"product_id": balance.product_id, "warehouse_id": balance.warehouse_id,
                           "location_id": balance.location_id, "expected": Decimal("0"), "actual": balance.quantity})
        if repair:
            balance.quantity = Decimal("0")
    if repair:
        session.commit()
    return mismatches
