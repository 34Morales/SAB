"""HTTP routes for the initial ERP core."""

from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from backend.app.api.dependencies import current_access, require_permission
from backend.app.database.session import get_db
from backend.app.modules.companies.models import Company
from backend.app.modules.companies import service
from backend.app.modules.companies.branch_schemas import BranchCreate, BranchRead, BranchUpdate
from backend.app.modules.companies.schemas import CompanyCreate, CompanyRead, CompanySettingsUpdate
from backend.app.modules.catalog import service as catalog_service
from backend.app.modules.catalog.models import Product
from backend.app.modules.catalog.schemas import (
    CategoryCreate,
    CategoryRead,
    CategoryUpdate,
    ProductCreate,
    ProductRead,
    ProductUpdate,
)
from backend.app.modules.catalog.service import CatalogCategoryNotFound
from backend.app.modules.identity.models import Membership, Role, SecurityEvent, User
from backend.app.modules.identity.schemas import (
    LoginRequest,
    MembershipRead,
    MembershipUpdate,
    MfaCodeRequest,
    MfaDisableRequest,
    MfaEnabledResponse,
    MfaSetupResponse,
    PasswordResetRequest,
    RecoveryAccepted,
    RecoveryRequest,
    RoleCreate,
    RoleRead,
    RoleUpdate,
    SecurityEventRead,
    SessionRead,
    UserCreate,
    UserRead,
)
from backend.app.modules.identity.service import (
    AccessContext,
    LoginFailure,
    authenticate,
    begin_mfa_setup,
    confirm_mfa_setup,
    create_member,
    disable_mfa,
    request_password_recovery,
    reset_password,
    revoke_token,
    update_membership,
    create_custom_role,
    delete_custom_role,
    update_custom_role,
)
from backend.app.modules.inventory import service as inventory_service
from backend.app.modules.inventory.models import InventoryBalance, InventoryMovement, InventoryOperation, Warehouse
from backend.app.modules.inventory.schemas import BalanceRead, InventoryOperationRequest, LocationCreate, LocationRead, MovementRead, OperationRead, WarehouseCreate, WarehouseRead
from backend.app.modules.inventory.service import InventoryError
from backend.app.modules.purchasing import service as purchasing_service
from backend.app.modules.purchasing.models import (
    PurchaseOrder, PurchaseOrderEvent, PurchaseReceipt, PurchaseReceiptLine, SupplierInvoice, SupplierPayment,
    SupplierReturn, SupplierReturnLine,
)
from backend.app.modules.purchasing.schemas import (
    PaymentCreate, PaymentRead, PurchaseOrderCreate, PurchaseOrderEventRead, PurchaseOrderRead,
    PurchaseOrderUpdate, ReceiptCreate, ReceiptLineRead, ReceiptRead, SupplierCreate,
    SupplierDetailRead, SupplierInvoiceCreate, SupplierInvoiceLineRead, SupplierInvoiceRead, SupplierRead,
    SupplierReturnCreate, SupplierReturnLineRead, SupplierReturnRead, SupplierUpdate,
)
from backend.app.modules.purchasing.service import PurchasingError
from backend.app.modules.sales import service as sales_service
from backend.app.modules.sales.models import (
    Customer, Quote, SalesCreditNote, SalesDelivery, SalesDeliveryLine, SalesEvent,
    SalesInvoice, SalesInvoiceLine, SalesOrder, SalesPayment, SalesReturn,
)
from backend.app.modules.sales.schemas import (
    CustomerCreate, CustomerRead, CustomerUpdate, DeliveryCreate, DeliveryLineRead,
    DeliveryRead, InvoiceCreate, InvoiceRead, OrderCreate, OrderRead, PaymentCreate as SalesPaymentCreate,
    PaymentRead as SalesPaymentRead, QuoteConvert, QuoteCreate, QuoteRead, QuoteUpdate,
    ReturnCreate, ReturnRead, StockAvailabilityRead,
)
from backend.app.modules.sales.service import SalesError

router = APIRouter()


def _audit_inventory_rejection(session: Session, request: Request, context: AccessContext, event_type: str, details: dict) -> None:
    session.add(SecurityEvent(
        user_id=context.user.id, company_id=context.membership.company_id, event_type=event_type,
        source_ip=request.client.host if request.client else None, details=details,
    ))
    session.commit()


@router.get("/inventory/warehouses", response_model=list[WarehouseRead], tags=["inventory"])
def inventory_warehouses(context: AccessContext = Depends(require_permission("inventory:read")), session: Session = Depends(get_db)):
    return inventory_service.list_warehouses(session, context.membership.company_id)


@router.post("/inventory/warehouses", response_model=WarehouseRead, status_code=201, tags=["inventory"])
def create_inventory_warehouse(data: WarehouseCreate, request: Request, context: AccessContext = Depends(require_permission("inventory:manage")), session: Session = Depends(get_db)):
    try:
        return inventory_service.create_warehouse(session, context.membership.company_id, data, context.user.id, request.client.host if request.client else None)
    except InventoryError as exc:
        _audit_inventory_rejection(session, request, context, "inventory_warehouse_create_rejected", {"reason": str(exc)})
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Warehouse code already exists") from exc


@router.patch("/inventory/warehouses/{warehouse_id}/deactivate", response_model=WarehouseRead, tags=["inventory"])
def deactivate_inventory_warehouse(warehouse_id: int, request: Request, context: AccessContext = Depends(require_permission("inventory:manage")), session: Session = Depends(get_db)):
    try:
        result = inventory_service.deactivate_warehouse(session, context.membership.company_id, warehouse_id, context.user.id, request.client.host if request.client else None)
    except InventoryError as exc:
        _audit_inventory_rejection(session, request, context, "inventory_warehouse_deactivation_rejected", {"warehouse_id": warehouse_id, "reason": str(exc)})
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Warehouse not found")
    return result


@router.get("/inventory/locations", response_model=list[LocationRead], tags=["inventory"])
def inventory_locations(warehouse_id: int = Query(gt=0), context: AccessContext = Depends(require_permission("inventory:read")), session: Session = Depends(get_db)):
    if session.scalar(select(Warehouse.id).where(Warehouse.id == warehouse_id, Warehouse.company_id == context.membership.company_id)) is None:
        raise HTTPException(status_code=404, detail="Warehouse not found")
    return inventory_service.list_locations(session, context.membership.company_id, warehouse_id)


@router.post("/inventory/locations", response_model=LocationRead, status_code=201, tags=["inventory"])
def create_inventory_location(data: LocationCreate, request: Request, context: AccessContext = Depends(require_permission("inventory:manage")), session: Session = Depends(get_db)):
    try:
        return inventory_service.create_location(session, context.membership.company_id, data, context.user.id, request.client.host if request.client else None)
    except InventoryError as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Location code already exists in this warehouse") from exc


@router.post("/inventory/operations", response_model=OperationRead, status_code=201, tags=["inventory"])
def post_inventory_operation(data: InventoryOperationRequest, request: Request, idempotency_key: str = Header(alias="Idempotency-Key"), context: AccessContext = Depends(require_permission("inventory:manage")), session: Session = Depends(get_db)):
    try:
        operation = inventory_service.post_operation(session, context.membership.company_id, data, idempotency_key, context.user.id, request.client.host if request.client else None)
    except InventoryError as exc:
        session.rollback()
        _audit_inventory_rejection(session, request, context, "inventory_operation_rejected", {"kind": data.kind, "product_id": data.product_id, "reason": str(exc)})
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Inventory operation conflicts with current state") from exc
    movements = session.scalars(select(InventoryMovement).where(InventoryMovement.operation_id == operation.id).order_by(InventoryMovement.id)).all()
    return OperationRead(id=operation.id, kind=operation.kind, movements=[_inventory_movement_read(movement) for movement in movements])


@router.get("/inventory/balances", response_model=list[BalanceRead], tags=["inventory"])
def inventory_balances(warehouse_id: int | None = Query(default=None, gt=0), product_id: int | None = Query(default=None, gt=0), branch_id: int | None = Query(default=None, gt=0), limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0), context: AccessContext = Depends(require_permission("inventory:read")), session: Session = Depends(get_db)):
    statement = select(InventoryBalance).where(InventoryBalance.company_id == context.membership.company_id)
    if warehouse_id is not None:
        statement = statement.where(InventoryBalance.warehouse_id == warehouse_id)
    if product_id is not None:
        statement = statement.where(InventoryBalance.product_id == product_id)
    if branch_id is not None:
        statement = statement.join(Warehouse, Warehouse.id == InventoryBalance.warehouse_id).where(Warehouse.branch_id == branch_id)
    return session.scalars(statement.order_by(InventoryBalance.product_id, InventoryBalance.warehouse_id, InventoryBalance.location_id).offset(offset).limit(limit)).all()


@router.get("/inventory/balances/below-minimum", response_model=list[BalanceRead], tags=["inventory"])
def inventory_below_minimum(context: AccessContext = Depends(require_permission("inventory:read")), session: Session = Depends(get_db)):
    return session.scalars(select(InventoryBalance).where(InventoryBalance.company_id == context.membership.company_id, InventoryBalance.minimum_quantity > 0, InventoryBalance.quantity < InventoryBalance.minimum_quantity).order_by(InventoryBalance.quantity)).all()


@router.patch("/inventory/balances/{balance_id}/thresholds", response_model=BalanceRead, tags=["inventory"])
def set_inventory_thresholds(balance_id: int, minimum_quantity: Decimal = Query(ge=0), reorder_point: Decimal = Query(ge=0), context: AccessContext = Depends(require_permission("inventory:manage")), session: Session = Depends(get_db)):
    balance = session.scalar(select(InventoryBalance).where(InventoryBalance.id == balance_id, InventoryBalance.company_id == context.membership.company_id).with_for_update())
    if balance is None:
        raise HTTPException(status_code=404, detail="Inventory position not found")
    if reorder_point < minimum_quantity:
        raise HTTPException(status_code=422, detail="Reorder point must be at least the minimum quantity")
    balance.minimum_quantity = minimum_quantity
    balance.reorder_point = reorder_point
    session.add(SecurityEvent(user_id=context.user.id, company_id=context.membership.company_id, event_type="inventory_threshold_updated", details={"balance_id": balance_id}))
    session.commit()
    session.refresh(balance)
    return balance


@router.get("/inventory/movements", response_model=list[MovementRead], tags=["inventory"])
def inventory_movements(product_id: int | None = Query(default=None, gt=0), warehouse_id: int | None = Query(default=None, gt=0), limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0), context: AccessContext = Depends(require_permission("inventory:read")), session: Session = Depends(get_db)):
    statement = select(InventoryMovement).where(InventoryMovement.company_id == context.membership.company_id)
    if product_id is not None:
        statement = statement.where(InventoryMovement.product_id == product_id)
    if warehouse_id is not None:
        statement = statement.where(InventoryMovement.warehouse_id == warehouse_id)
    movements = session.scalars(statement.order_by(InventoryMovement.id.desc()).offset(offset).limit(limit)).all()
    return [_inventory_movement_read(movement) for movement in movements]


def _inventory_movement_read(movement: InventoryMovement) -> MovementRead:
    operation = movement.operation
    return MovementRead(
        id=movement.id, operation_id=movement.operation_id, product_id=movement.product_id,
        warehouse_id=movement.warehouse_id, location_id=movement.location_id,
        movement_type=movement.movement_type, direction=movement.direction, quantity=movement.quantity,
        unit_cost=movement.unit_cost, created_at=movement.created_at, reason=operation.reason,
        notes=operation.notes, reference_type=operation.reference_type,
        reference_id=operation.reference_id, actor_user_id=operation.actor_user_id,
    )


@router.get("/inventory/summary", tags=["inventory"])
def inventory_summary(context: AccessContext = Depends(require_permission("inventory:read")), session: Session = Depends(get_db)):
    rows = session.execute(select(InventoryBalance.product_id, InventoryBalance.warehouse_id, InventoryBalance.location_id, InventoryBalance.quantity).where(InventoryBalance.company_id == context.membership.company_id).order_by(InventoryBalance.product_id, InventoryBalance.warehouse_id)).all()
    return [{"product_id": p, "warehouse_id": w, "location_id": loc, "quantity": qty} for p, w, loc, qty in rows]


@router.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/companies", response_model=CompanyRead, status_code=status.HTTP_201_CREATED, tags=["companies"])
def create_company(
    data: CompanyCreate,
    request: Request,
    context: AccessContext = Depends(require_permission("companies:create")),
    session: Session = Depends(get_db),
) -> CompanyRead:
    company = Company(**data.model_dump())
    role = session.scalar(select(Role).where(Role.name == "admin", Role.company_id.is_(None)))
    if role is None:
        raise HTTPException(status_code=503, detail="Default administrator role is unavailable")
    session.add(company)
    session.flush()
    session.add(Membership(user_id=context.user.id, company=company, roles=[role]))
    session.add(SecurityEvent(
        user_id=context.user.id, company_id=company.id, event_type="company_created",
        source_ip=request.client.host if request.client else None,
        details={"company_id": company.id},
    ))
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Company could not be created") from exc
    session.refresh(company)
    return company


@router.get("/companies", response_model=list[CompanyRead], tags=["companies"])
def list_companies(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    context: AccessContext = Depends(require_permission("companies:read")),
    session: Session = Depends(get_db),
) -> list[CompanyRead]:
    company_ids = [membership.company_id for membership in context.user.memberships if membership.is_active]
    return service.list_companies(session, company_ids, limit, offset)


@router.get("/companies/current", response_model=CompanyRead, tags=["companies"])
def get_current_company(
    context: AccessContext = Depends(require_permission("companies:read")),
    session: Session = Depends(get_db),
) -> CompanyRead:
    company = service.get_company(session, context.membership.company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


@router.patch("/companies/current", response_model=CompanyRead, tags=["companies"])
def update_current_company_settings(
    data: CompanySettingsUpdate,
    request: Request,
    context: AccessContext = Depends(require_permission("companies:manage")),
    session: Session = Depends(get_db),
) -> CompanyRead:
    company = service.update_company_settings(
        session,
        context.membership.company_id,
        data,
        context.user.id,
        request.client.host if request.client else None,
    )
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


@router.get("/branches", response_model=list[BranchRead], tags=["companies"])
def list_branches(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    context: AccessContext = Depends(require_permission("companies:read")),
    session: Session = Depends(get_db),
) -> list[BranchRead]:
    return service.list_branches(session, context.membership.company_id, limit, offset)


@router.post("/branches", response_model=BranchRead, status_code=201, tags=["companies"])
def create_branch(
    data: BranchCreate,
    request: Request,
    context: AccessContext = Depends(require_permission("companies:manage")),
    session: Session = Depends(get_db),
) -> BranchRead:
    try:
        return service.create_branch(
            session,
            context.membership.company_id,
            data,
            context.user.id,
            request.client.host if request.client else None,
        )
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Branch code already exists in this company") from exc


@router.patch("/branches/{branch_id}", response_model=BranchRead, tags=["companies"])
def update_branch(
    branch_id: int,
    data: BranchUpdate,
    request: Request,
    context: AccessContext = Depends(require_permission("companies:manage")),
    session: Session = Depends(get_db),
) -> BranchRead:
    try:
        branch = service.update_branch(
            session,
            context.membership.company_id,
            branch_id,
            data,
            context.user.id,
            request.client.host if request.client else None,
        )
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Branch code already exists in this company") from exc
    if branch is None:
        raise HTTPException(status_code=404, detail="Branch not found")
    return branch


@router.get("/catalog/categories", response_model=list[CategoryRead], tags=["catalog"])
def list_catalog_categories(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    context: AccessContext = Depends(require_permission("catalog:read")),
    session: Session = Depends(get_db),
) -> list[CategoryRead]:
    return catalog_service.list_categories(session, context.membership.company_id, limit, offset)


@router.post("/catalog/categories", response_model=CategoryRead, status_code=201, tags=["catalog"])
def create_catalog_category(
    data: CategoryCreate,
    request: Request,
    context: AccessContext = Depends(require_permission("catalog:manage")),
    session: Session = Depends(get_db),
) -> CategoryRead:
    try:
        return catalog_service.create_category(
            session, context.membership.company_id, data, context.user.id,
            request.client.host if request.client else None,
        )
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Category name already exists") from exc


@router.patch("/catalog/categories/{category_id}", response_model=CategoryRead, tags=["catalog"])
def update_catalog_category(
    category_id: int,
    data: CategoryUpdate,
    request: Request,
    context: AccessContext = Depends(require_permission("catalog:manage")),
    session: Session = Depends(get_db),
) -> CategoryRead:
    try:
        category = catalog_service.update_category(
            session, context.membership.company_id, category_id, data, context.user.id,
            request.client.host if request.client else None,
        )
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Category name already exists") from exc
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")
    return category


@router.get("/catalog/products", response_model=list[ProductRead], tags=["catalog"])
def list_catalog_products(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    category_id: int | None = Query(default=None, gt=0),
    search: str | None = Query(default=None, min_length=1, max_length=100),
    context: AccessContext = Depends(require_permission("catalog:read")),
    session: Session = Depends(get_db),
) -> list[ProductRead]:
    return catalog_service.list_products(
        session, context.membership.company_id, limit, offset, category_id, search,
    )


@router.post("/catalog/products", response_model=ProductRead, status_code=201, tags=["catalog"])
def create_catalog_product(
    data: ProductCreate,
    request: Request,
    context: AccessContext = Depends(require_permission("catalog:manage")),
    session: Session = Depends(get_db),
) -> ProductRead:
    try:
        return catalog_service.create_product(
            session, context.membership.company_id, data, context.user.id,
            request.client.host if request.client else None,
        )
    except CatalogCategoryNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Product SKU already exists") from exc


@router.patch("/catalog/products/{product_id}", response_model=ProductRead, tags=["catalog"])
def update_catalog_product(
    product_id: int,
    data: ProductUpdate,
    request: Request,
    context: AccessContext = Depends(require_permission("catalog:manage")),
    session: Session = Depends(get_db),
) -> ProductRead:
    try:
        product = catalog_service.update_product(
            session, context.membership.company_id, product_id, data, context.user.id,
            request.client.host if request.client else None,
        )
    except CatalogCategoryNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Product SKU already exists") from exc
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@router.post("/auth/login", response_model=SessionRead, tags=["identity"])
def login(
    data: LoginRequest,
    request: Request,
    response: Response,
    company_id: int | None = Header(default=None, alias="X-Company-ID", gt=0),
    session: Session = Depends(get_db),
) -> SessionRead:
    source_ip = request.client.host if request.client else None
    try:
        result = authenticate(session, data.email, data.password, source_ip, data.otp_code, data.recovery_code, company_id)
    except LoginFailure as exc:
        if exc.reason == "rate_limited":
            raise HTTPException(status_code=429, detail="Too many attempts", headers={"Retry-After": str(exc.retry_after)}) from exc
        if exc.reason == "mfa_required":
            raise HTTPException(status_code=401, detail="MFA verification required") from exc
        raise HTTPException(status_code=401, detail="Invalid email, password, or MFA code") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Authentication security configuration is unavailable") from exc
    token, expires_in = result
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return SessionRead(access_token=token, expires_in=expires_in)


@router.post("/auth/logout", status_code=204, tags=["identity"])
def logout(
    request: Request,
    authorization: str = Header(alias="Authorization"),
    session: Session = Depends(get_db),
) -> None:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Bearer token required")
    revoke_token(session, token, request.client.host if request and request.client else None)


@router.get("/auth/me", response_model=UserRead, tags=["identity"])
def who_am_i(context: AccessContext = Depends(current_access)) -> UserRead:
    return context.user


@router.get("/identity/roles", response_model=list[RoleRead], tags=["identity"])
def list_roles(
    context: AccessContext = Depends(require_permission("identity:read")),
    session: Session = Depends(get_db),
) -> list[RoleRead]:
    roles = session.scalars(
        select(Role).where((Role.company_id == context.membership.company_id) | (Role.company_id.is_(None))).order_by(Role.name)
    ).unique().all()
    return [RoleRead(id=role.id, name=role.name, permissions=sorted(p.code for p in role.permissions)) for role in roles]


@router.post("/identity/roles", response_model=RoleRead, status_code=201, tags=["identity"])
def create_role(data: RoleCreate, request: Request, context: AccessContext = Depends(require_permission("identity:manage")), session: Session = Depends(get_db)) -> RoleRead:
    try:
        role = create_custom_role(session, context.membership.company_id, data, context.user.id, request.client.host if request.client else None)
    except LookupError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Role name already exists in this company") from exc
    return RoleRead(id=role.id, name=role.name, permissions=sorted(permission.code for permission in role.permissions))


@router.patch("/identity/roles/{role_id}", response_model=RoleRead, tags=["identity"])
def change_role(role_id: int, data: RoleUpdate, request: Request, context: AccessContext = Depends(require_permission("identity:manage")), session: Session = Depends(get_db)) -> RoleRead:
    try:
        role = update_custom_role(session, context.membership.company_id, role_id, data, context.user.id, request.client.host if request.client else None)
    except LookupError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        session.rollback()
        session.add(SecurityEvent(user_id=context.user.id, company_id=context.membership.company_id,
            event_type="identity_role_update_rejected", source_ip=request.client.host if request.client else None,
            details={"role_id": role_id, "reason": str(exc)}))
        session.commit()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Role name already exists in this company") from exc
    if role is None:
        raise HTTPException(status_code=404, detail="Custom role not found")
    return RoleRead(id=role.id, name=role.name, permissions=sorted(permission.code for permission in role.permissions))


@router.delete("/identity/roles/{role_id}", status_code=204, tags=["identity"])
def remove_role(role_id: int, request: Request, context: AccessContext = Depends(require_permission("identity:manage")), session: Session = Depends(get_db)) -> None:
    try:
        deleted = delete_custom_role(
            session, context.membership.company_id, role_id, context.user.id,
            request.client.host if request.client else None,
        )
    except ValueError as exc:
        session.rollback()
        session.add(SecurityEvent(user_id=context.user.id, company_id=context.membership.company_id,
            event_type="identity_role_delete_rejected", source_ip=request.client.host if request.client else None,
            details={"role_id": role_id, "reason": str(exc)}))
        session.commit()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if deleted is None:
        raise HTTPException(status_code=404, detail="Custom role not found")


@router.get("/identity/users", response_model=list[UserRead], tags=["identity"])
def list_users(
    context: AccessContext = Depends(require_permission("identity:read")),
    session: Session = Depends(get_db),
) -> list[UserRead]:
    users = session.scalars(
        select(User)
        .join(Membership)
        .where(Membership.company_id == context.membership.company_id, Membership.is_active.is_(True))
        .order_by(User.id)
    ).unique().all()
    return list(users)


@router.get("/identity/memberships", response_model=list[MembershipRead], tags=["identity"])
def list_memberships(
    context: AccessContext = Depends(require_permission("identity:read")),
    session: Session = Depends(get_db),
) -> list[MembershipRead]:
    memberships = session.scalars(
        select(Membership).where(Membership.company_id == context.membership.company_id)
        .options(joinedload(Membership.roles).joinedload(Role.permissions))
        .order_by(Membership.id)
    ).unique().all()
    return [_membership_read(membership) for membership in memberships]


@router.patch("/identity/memberships/{membership_id}", response_model=MembershipRead, tags=["identity"])
def change_membership(
    membership_id: int,
    data: MembershipUpdate,
    request: Request,
    context: AccessContext = Depends(require_permission("identity:manage")),
    session: Session = Depends(get_db),
) -> MembershipRead:
    try:
        membership = update_membership(
            session, context.membership.company_id, membership_id,
            data.model_dump(exclude_unset=True), context.user.id,
            request.client.host if request.client else None,
        )
    except LookupError as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        session.rollback()
        session.add(SecurityEvent(user_id=context.user.id, company_id=context.membership.company_id,
            event_type="membership_update_rejected", source_ip=request.client.host if request.client else None,
            details={"membership_id": membership_id, "reason": str(exc)}))
        session.commit()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if membership is None:
        session.add(SecurityEvent(user_id=context.user.id, company_id=context.membership.company_id,
            event_type="membership_update_rejected", source_ip=request.client.host if request.client else None,
            details={"membership_id": membership_id, "reason": "not_found_in_company"}))
        session.commit()
        raise HTTPException(status_code=404, detail="Membership not found")
    return _membership_read(membership)


def _membership_read(membership: Membership) -> MembershipRead:
    return MembershipRead(
        id=membership.id, user_id=membership.user_id, company_id=membership.company_id,
        is_active=membership.is_active,
        roles=[RoleRead(id=role.id, name=role.name, permissions=sorted(permission.code for permission in role.permissions)) for role in membership.roles],
    )


@router.post("/identity/users", response_model=UserRead, status_code=201, tags=["identity"])
def create_user(
    data: UserCreate,
    request: Request,
    context: AccessContext = Depends(require_permission("identity:manage")),
    session: Session = Depends(get_db),
) -> UserRead:
    try:
        return create_member(
            session, context.membership.company_id, data, context.user.id,
            request.client.host if request.client else None,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="User email already exists") from exc


@router.post("/auth/mfa/setup", response_model=MfaSetupResponse, tags=["identity"])
def start_mfa_setup(
    request: Request,
    response: Response,
    context: AccessContext = Depends(current_access),
    session: Session = Depends(get_db),
) -> MfaSetupResponse:
    try:
        secret, uri = begin_mfa_setup(
            session, context.user, context.membership.company_id, request.client.host if request.client else None
        )
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="MFA encryption is not configured") from exc
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return MfaSetupResponse(secret=secret, otpauth_uri=uri)


@router.post("/auth/mfa/confirm", response_model=MfaEnabledResponse, tags=["identity"])
def confirm_mfa(
    data: MfaCodeRequest,
    request: Request,
    response: Response,
    context: AccessContext = Depends(current_access),
    session: Session = Depends(get_db),
) -> MfaEnabledResponse:
    try:
        codes = confirm_mfa_setup(
            session, context.user.id, context.membership.company_id, data.code,
            request.client.host if request.client else None,
        )
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LoginFailure as exc:
        if exc.reason == "rate_limited":
            raise HTTPException(status_code=429, detail="Too many attempts", headers={"Retry-After": str(exc.retry_after)}) from exc
        raise HTTPException(status_code=401, detail="Invalid MFA verification code") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid MFA verification code") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="MFA encryption is unavailable") from exc
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return MfaEnabledResponse(enabled=True, recovery_codes=codes)


@router.post("/auth/mfa/disable", status_code=204, tags=["identity"])
def turn_off_mfa(
    data: MfaDisableRequest,
    request: Request,
    context: AccessContext = Depends(current_access),
    session: Session = Depends(get_db),
) -> Response:
    try:
        disable_mfa(
            session, context.user.id, context.membership.company_id, data.password, data.otp_code,
            data.recovery_code, request.client.host if request.client else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid credentials or MFA factor") from exc
    except LoginFailure as exc:
        if exc.reason == "rate_limited":
            raise HTTPException(status_code=429, detail="Too many attempts", headers={"Retry-After": str(exc.retry_after)}) from exc
        raise HTTPException(status_code=401, detail="Invalid credentials or MFA factor") from exc
    return Response(status_code=204)


@router.post("/auth/password-recovery", response_model=RecoveryAccepted, status_code=202, tags=["identity"])
def request_recovery(data: RecoveryRequest, request: Request, session: Session = Depends(get_db)) -> RecoveryAccepted:
    try:
        request_password_recovery(session, data.email, request.client.host if request.client else None)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Recovery is temporarily unavailable") from exc
    return RecoveryAccepted(message="If the account exists, recovery instructions will be sent.")


@router.post("/auth/password-reset", status_code=204, tags=["identity"])
def finish_recovery(data: PasswordResetRequest, request: Request, session: Session = Depends(get_db)) -> Response:
    try:
        reset_password(session, data.token, data.new_password, request.client.host if request.client else None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid or expired recovery token") from exc
    return Response(status_code=204)


@router.get("/auth/password-reset-page", response_class=HTMLResponse, include_in_schema=False)
def password_reset_page() -> HTMLResponse:
    import secrets

    nonce = secrets.token_urlsafe(18)
    page = """<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Restablecer contraseña</title><body><main><h1>Restablecer contraseña</h1>
<form id="reset"><label>Nueva contraseña <input id="password" type="password" minlength="12" maxlength="1024" required autocomplete="new-password"></label>
<button type="submit">Guardar contraseña</button></form><p id="message" role="status" aria-live="polite"></p></main>
<script nonce="__NONCE__">const token=new URLSearchParams(location.hash.slice(1)).get('token');history.replaceState(null,'',location.pathname);
const form=document.getElementById('reset'),message=document.getElementById('message');
form.addEventListener('submit',async(event)=>{event.preventDefault();if(!token){message.textContent='El enlace no es válido o ya venció.';return;}
const response=await fetch('/api/v1/auth/password-reset',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token,new_password:document.getElementById('password').value})});
message.textContent=response.ok?'Contraseña actualizada. Ya puedes iniciar sesión.':'El enlace no es válido o ya venció.';if(response.ok)form.remove();});
if(!token){message.textContent='El enlace no es válido o ya venció.';form.remove();}</script></body></html>""".replace("__NONCE__", nonce)
    return HTMLResponse(
        page,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": f"default-src 'none'; script-src 'nonce-{nonce}'; connect-src 'self'; form-action 'none'; base-uri 'none'; frame-ancestors 'none'",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/security/events", response_model=list[SecurityEventRead], tags=["security"])
def list_security_events(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    context: AccessContext = Depends(require_permission("security:audit:read")),
    session: Session = Depends(get_db),
) -> list[SecurityEventRead]:
    events = session.scalars(
        select(SecurityEvent)
        .where(SecurityEvent.company_id == context.membership.company_id)
        .order_by(SecurityEvent.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return [SecurityEventRead(
        id=event.id, user_id=event.user_id, company_id=event.company_id, event_type=event.event_type,
        source_ip=event.source_ip, details=event.details, created_at=event.created_at.isoformat(),
    ) for event in events]


def _audit_purchasing_rejection(session: Session, request: Request, context: AccessContext, event_type: str, details: dict) -> None:
    session.add(SecurityEvent(
        user_id=context.user.id, company_id=context.membership.company_id, event_type=event_type,
        source_ip=request.client.host if request.client else None, details=details,
    ))
    session.commit()


def _invoice_read(invoice: SupplierInvoice) -> SupplierInvoiceRead:
    return SupplierInvoiceRead(
        id=invoice.id, supplier_id=invoice.supplier_id, purchase_order_id=invoice.purchase_order_id,
        receipt_id=invoice.receipt_id, document_number=invoice.document_number,
        issued_date=invoice.issued_date, due_date=invoice.due_date, currency_code=invoice.currency_code,
        subtotal=invoice.subtotal, tax_total=invoice.tax_total, total=invoice.total,
        paid_amount=invoice.paid_amount, balance_due=invoice.total - invoice.paid_amount,
        status=invoice.status, notes=invoice.notes,
        lines=[SupplierInvoiceLineRead(
            id=line.id, purchase_order_line_id=line.purchase_order_line_id, receipt_line_id=line.receipt_line_id,
            description=line.description, quantity=line.quantity, unit_price=line.unit_price,
            tax_rate=line.tax_rate, subtotal=line.subtotal, tax_amount=line.tax_amount, total=line.total,
        ) for line in invoice.lines],
    )


@router.get("/purchasing/suppliers", response_model=list[SupplierRead], tags=["purchasing"])
def list_suppliers(
    q: str | None = Query(default=None, max_length=120),
    active: bool | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    context: AccessContext = Depends(require_permission("purchasing:read")),
    session: Session = Depends(get_db),
) -> list[SupplierRead]:
    return purchasing_service.list_suppliers(session, context.membership.company_id, limit, offset, q, active)


@router.post("/purchasing/suppliers", response_model=SupplierRead, status_code=201, tags=["purchasing"])
def create_supplier(
    data: SupplierCreate,
    request: Request,
    context: AccessContext = Depends(require_permission("purchasing:manage")),
    session: Session = Depends(get_db),
) -> SupplierRead:
    try:
        return purchasing_service.create_supplier(session, context.membership.company_id, data, context.user.id, request.client.host if request.client else None)
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Supplier code already exists in this company") from exc


@router.get("/purchasing/suppliers/{supplier_id}", response_model=SupplierDetailRead, tags=["purchasing"])
def get_supplier(
    supplier_id: int,
    context: AccessContext = Depends(require_permission("purchasing:manage")),
    session: Session = Depends(get_db),
) -> SupplierDetailRead:
    supplier = purchasing_service.get_supplier(session, context.membership.company_id, supplier_id)
    if supplier is None:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return supplier


@router.patch("/purchasing/suppliers/{supplier_id}", response_model=SupplierRead, tags=["purchasing"])
def update_supplier(
    supplier_id: int,
    data: SupplierUpdate,
    request: Request,
    context: AccessContext = Depends(require_permission("purchasing:manage")),
    session: Session = Depends(get_db),
) -> SupplierRead:
    try:
        supplier = purchasing_service.update_supplier(session, context.membership.company_id, supplier_id, data, context.user.id, request.client.host if request.client else None)
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Supplier update conflicts with existing data") from exc
    if supplier is None:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return supplier


@router.get("/purchasing/orders", response_model=list[PurchaseOrderRead], tags=["purchasing"])
def list_purchase_orders(
    status_filter: str | None = Query(default=None, alias="status", pattern=r"^(draft|confirmed|cancelled|closed)$"),
    supplier_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    context: AccessContext = Depends(require_permission("purchasing:read")),
    session: Session = Depends(get_db),
) -> list[PurchaseOrderRead]:
    return purchasing_service.list_purchase_orders(session, context.membership.company_id, limit, offset, status_filter, supplier_id)


@router.post("/purchasing/orders", response_model=PurchaseOrderRead, status_code=201, tags=["purchasing"])
def create_purchase_order(
    data: PurchaseOrderCreate,
    request: Request,
    context: AccessContext = Depends(require_permission("purchasing:manage")),
    session: Session = Depends(get_db),
) -> PurchaseOrderRead:
    try:
        order = purchasing_service.create_purchase_order(session, context.membership.company_id, data, context.user.id, request.client.host if request.client else None)
    except PurchasingError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Purchase order number already exists in this company") from exc
    return order


@router.get("/purchasing/orders/{order_id}", response_model=PurchaseOrderRead, tags=["purchasing"])
def get_purchase_order(
    order_id: int,
    context: AccessContext = Depends(require_permission("purchasing:read")),
    session: Session = Depends(get_db),
) -> PurchaseOrderRead:
    order = purchasing_service.get_purchase_order(session, context.membership.company_id, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    return order


@router.patch("/purchasing/orders/{order_id}", response_model=PurchaseOrderRead, tags=["purchasing"])
def update_purchase_order(
    order_id: int,
    data: PurchaseOrderUpdate,
    request: Request,
    context: AccessContext = Depends(require_permission("purchasing:manage")),
    session: Session = Depends(get_db),
) -> PurchaseOrderRead:
    try:
        order = purchasing_service.update_purchase_order(session, context.membership.company_id, order_id, data, context.user.id, request.client.host if request.client else None)
    except PurchasingError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Purchase order update conflicts with existing data") from exc
    if order is None:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    return order


def _transition_order(order_id: int, action: str, request: Request, context: AccessContext, session: Session) -> PurchaseOrderRead:
    try:
        order = purchasing_service.transition_purchase_order(session, context.membership.company_id, order_id, action, context.user.id, request.client.host if request.client else None)
    except PurchasingError as exc:
        session.rollback()
        _audit_purchasing_rejection(session, request, context, "purchase_order_transition_rejected", {"purchase_order_id": order_id, "action": action, "reason": str(exc)})
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if order is None:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    return order


@router.post("/purchasing/orders/{order_id}/confirm", response_model=PurchaseOrderRead, tags=["purchasing"])
def confirm_purchase_order(order_id: int, request: Request, context: AccessContext = Depends(require_permission("purchasing:manage")), session: Session = Depends(get_db)):
    return _transition_order(order_id, "confirm", request, context, session)


@router.post("/purchasing/orders/{order_id}/cancel", response_model=PurchaseOrderRead, tags=["purchasing"])
def cancel_purchase_order(order_id: int, request: Request, context: AccessContext = Depends(require_permission("purchasing:manage")), session: Session = Depends(get_db)):
    return _transition_order(order_id, "cancel", request, context, session)


@router.post("/purchasing/orders/{order_id}/close", response_model=PurchaseOrderRead, tags=["purchasing"])
def close_purchase_order(order_id: int, request: Request, context: AccessContext = Depends(require_permission("purchasing:manage")), session: Session = Depends(get_db)):
    return _transition_order(order_id, "close", request, context, session)


@router.get("/purchasing/orders/{order_id}/events", response_model=list[PurchaseOrderEventRead], tags=["purchasing"])
def purchase_order_events(order_id: int, context: AccessContext = Depends(require_permission("purchasing:read")), session: Session = Depends(get_db)):
    if session.scalar(select(PurchaseOrder.id).where(PurchaseOrder.id == order_id, PurchaseOrder.company_id == context.membership.company_id)) is None:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    return session.scalars(select(PurchaseOrderEvent).where(
        PurchaseOrderEvent.purchase_order_id == order_id, PurchaseOrderEvent.company_id == context.membership.company_id,
    ).order_by(PurchaseOrderEvent.id)).all()


@router.post("/purchasing/orders/{order_id}/receipts", response_model=ReceiptRead, status_code=201, tags=["purchasing"])
def receive_purchase_order(
    order_id: int,
    data: ReceiptCreate,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    context: AccessContext = Depends(require_permission("purchasing:manage")),
    session: Session = Depends(get_db),
) -> ReceiptRead:
    try:
        receipt = purchasing_service.receive_purchase_order(
            session, context.membership.company_id, order_id, data, idempotency_key,
            context.user.id, request.client.host if request.client else None,
        )
    except (PurchasingError, InventoryError) as exc:
        session.rollback()
        _audit_purchasing_rejection(session, request, context, "purchase_receipt_rejected", {"purchase_order_id": order_id, "reason": str(exc)})
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Receipt conflicts with current order or inventory state") from exc
    lines = session.scalars(select(PurchaseReceiptLine).where(PurchaseReceiptLine.receipt_id == receipt.id).order_by(PurchaseReceiptLine.id)).all()
    return ReceiptRead(
        id=receipt.id, purchase_order_id=receipt.purchase_order_id, warehouse_id=receipt.warehouse_id,
        notes=receipt.notes, actor_user_id=receipt.actor_user_id, created_at=receipt.created_at,
        lines=[ReceiptLineRead(
            id=line.id, purchase_order_line_id=line.purchase_order_line_id, product_id=line.product_id,
            location_id=line.location_id, accepted_quantity=line.accepted_quantity, damaged_quantity=line.damaged_quantity,
            notes=line.notes, inventory_operation_id=line.inventory_operation_id,
        ) for line in lines],
    )


@router.get("/purchasing/receipts/{receipt_id}", response_model=ReceiptRead, tags=["purchasing"])
def get_purchase_receipt(receipt_id: int, context: AccessContext = Depends(require_permission("purchasing:read")), session: Session = Depends(get_db)):
    receipt = purchasing_service.get_receipt(session, context.membership.company_id, receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Purchase receipt not found")
    return ReceiptRead(
        id=receipt.id, purchase_order_id=receipt.purchase_order_id, warehouse_id=receipt.warehouse_id,
        notes=receipt.notes, actor_user_id=receipt.actor_user_id, created_at=receipt.created_at,
        lines=[ReceiptLineRead(
            id=line.id, purchase_order_line_id=line.purchase_order_line_id, product_id=line.product_id,
            location_id=line.location_id, accepted_quantity=line.accepted_quantity, damaged_quantity=line.damaged_quantity,
            notes=line.notes, inventory_operation_id=line.inventory_operation_id,
        ) for line in receipt.lines],
    )


@router.get("/purchasing/receipts", response_model=list[ReceiptRead], tags=["purchasing"])
def list_purchase_receipts(
    purchase_order_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    context: AccessContext = Depends(require_permission("purchasing:read")), session: Session = Depends(get_db),
):
    statement = select(PurchaseReceipt).where(PurchaseReceipt.company_id == context.membership.company_id)
    if purchase_order_id is not None:
        statement = statement.where(PurchaseReceipt.purchase_order_id == purchase_order_id)
    receipts = session.scalars(statement.order_by(PurchaseReceipt.id.desc()).offset(offset).limit(limit)).all()
    result = []
    for receipt in receipts:
        lines = session.scalars(select(PurchaseReceiptLine).where(PurchaseReceiptLine.receipt_id == receipt.id).order_by(PurchaseReceiptLine.id)).all()
        result.append(ReceiptRead(
            id=receipt.id, purchase_order_id=receipt.purchase_order_id, warehouse_id=receipt.warehouse_id,
            notes=receipt.notes, actor_user_id=receipt.actor_user_id, created_at=receipt.created_at,
            lines=[ReceiptLineRead(
                id=line.id, purchase_order_line_id=line.purchase_order_line_id, product_id=line.product_id,
                location_id=line.location_id, accepted_quantity=line.accepted_quantity, damaged_quantity=line.damaged_quantity,
                notes=line.notes, inventory_operation_id=line.inventory_operation_id,
            ) for line in lines],
        ))
    return result


@router.post("/purchasing/receipts/{receipt_id}/returns", response_model=SupplierReturnRead, status_code=201, tags=["purchasing"])
def return_to_supplier(
    receipt_id: int, data: SupplierReturnCreate, request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    context: AccessContext = Depends(require_permission("purchasing:manage")), session: Session = Depends(get_db),
):
    try:
        supplier_return = purchasing_service.create_supplier_return(
            session, context.membership.company_id, receipt_id, data, idempotency_key,
            context.user.id, request.client.host if request.client else None,
        )
    except (PurchasingError, InventoryError) as exc:
        session.rollback()
        _audit_purchasing_rejection(session, request, context, "supplier_return_rejected", {"receipt_id": receipt_id, "reason": str(exc)})
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Supplier return conflicts with current inventory") from exc
    lines = session.scalars(select(SupplierReturnLine).where(SupplierReturnLine.supplier_return_id == supplier_return.id).order_by(SupplierReturnLine.id)).all()
    return SupplierReturnRead(
        id=supplier_return.id, receipt_id=supplier_return.receipt_id, reason=supplier_return.reason,
        actor_user_id=supplier_return.actor_user_id, created_at=supplier_return.created_at,
        lines=[SupplierReturnLineRead(id=line.id, receipt_line_id=line.receipt_line_id, quantity=line.quantity, inventory_operation_id=line.inventory_operation_id) for line in lines],
    )


@router.get("/purchasing/returns/{return_id}", response_model=SupplierReturnRead, tags=["purchasing"])
def get_supplier_return(return_id: int, context: AccessContext = Depends(require_permission("purchasing:read")), session: Session = Depends(get_db)):
    supplier_return = session.scalar(select(SupplierReturn).where(SupplierReturn.id == return_id, SupplierReturn.company_id == context.membership.company_id))
    if supplier_return is None:
        raise HTTPException(status_code=404, detail="Supplier return not found")
    lines = session.scalars(select(SupplierReturnLine).where(SupplierReturnLine.supplier_return_id == return_id).order_by(SupplierReturnLine.id)).all()
    return SupplierReturnRead(
        id=supplier_return.id, receipt_id=supplier_return.receipt_id, reason=supplier_return.reason,
        actor_user_id=supplier_return.actor_user_id, created_at=supplier_return.created_at,
        lines=[SupplierReturnLineRead(id=line.id, receipt_line_id=line.receipt_line_id, quantity=line.quantity, inventory_operation_id=line.inventory_operation_id) for line in lines],
    )


@router.get("/purchasing/invoices", response_model=list[SupplierInvoiceRead], tags=["purchasing"])
def list_supplier_invoices(
    supplier_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    context: AccessContext = Depends(require_permission("purchasing:read")),
    session: Session = Depends(get_db),
):
    return [_invoice_read(invoice) for invoice in purchasing_service.list_supplier_invoices(session, context.membership.company_id, limit, offset, supplier_id)]


@router.post("/purchasing/invoices", response_model=SupplierInvoiceRead, status_code=201, tags=["purchasing"])
def create_supplier_invoice(
    data: SupplierInvoiceCreate, request: Request,
    context: AccessContext = Depends(require_permission("purchasing:manage")), session: Session = Depends(get_db),
):
    try:
        invoice = purchasing_service.create_supplier_invoice(session, context.membership.company_id, data, context.user.id, request.client.host if request.client else None)
    except PurchasingError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Supplier invoice document number already exists") from exc
    return _invoice_read(invoice)


@router.get("/purchasing/invoices/{invoice_id}", response_model=SupplierInvoiceRead, tags=["purchasing"])
def get_supplier_invoice(invoice_id: int, context: AccessContext = Depends(require_permission("purchasing:read")), session: Session = Depends(get_db)):
    invoice = purchasing_service.get_supplier_invoice(session, context.membership.company_id, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail="Supplier invoice not found")
    return _invoice_read(invoice)


@router.post("/purchasing/invoices/{invoice_id}/payments", response_model=PaymentRead, status_code=201, tags=["purchasing"])
def post_supplier_payment(
    invoice_id: int, data: PaymentCreate, request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    context: AccessContext = Depends(require_permission("purchasing:manage")), session: Session = Depends(get_db),
):
    try:
        return purchasing_service.post_supplier_payment(
            session, context.membership.company_id, invoice_id, data, idempotency_key,
            context.user.id, request.client.host if request.client else None,
        )
    except PurchasingError as exc:
        session.rollback()
        _audit_purchasing_rejection(session, request, context, "supplier_payment_rejected", {"invoice_id": invoice_id, "reason": str(exc)})
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Payment conflicts with current invoice balance") from exc


@router.get("/purchasing/invoices/{invoice_id}/payments", response_model=list[PaymentRead], tags=["purchasing"])
def list_supplier_payments(invoice_id: int, context: AccessContext = Depends(require_permission("purchasing:read")), session: Session = Depends(get_db)):
    if session.scalar(select(SupplierInvoice.id).where(SupplierInvoice.id == invoice_id, SupplierInvoice.company_id == context.membership.company_id)) is None:
        raise HTTPException(status_code=404, detail="Supplier invoice not found")
    return session.scalars(select(SupplierPayment).where(
        SupplierPayment.invoice_id == invoice_id, SupplierPayment.company_id == context.membership.company_id,
    ).order_by(SupplierPayment.id)).all()


@router.post("/purchasing/invoices/{invoice_id}/void", response_model=SupplierInvoiceRead, tags=["purchasing"])
def void_supplier_invoice(invoice_id: int, request: Request, context: AccessContext = Depends(require_permission("purchasing:manage")), session: Session = Depends(get_db)):
    try:
        invoice = purchasing_service.void_supplier_invoice(session, context.membership.company_id, invoice_id, context.user.id, request.client.host if request.client else None)
    except PurchasingError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if invoice is None:
        raise HTTPException(status_code=404, detail="Supplier invoice not found")
    return _invoice_read(invoice)


def _sales_invoice_read(invoice: SalesInvoice) -> InvoiceRead:
    return InvoiceRead(
        id=invoice.id, customer_id=invoice.customer_id, order_id=invoice.order_id, number=invoice.number,
        issued_date=invoice.issued_date, due_date=invoice.due_date, currency_code=invoice.currency_code,
        subtotal=invoice.subtotal, tax_total=invoice.tax_total, total=invoice.total,
        paid_amount=invoice.paid_amount, credited_amount=invoice.credited_amount,
        balance_due=invoice.total - invoice.paid_amount - invoice.credited_amount, status=invoice.status,
        notes=invoice.notes, lines=[{
            "id": line.id, "order_line_id": line.order_line_id, "description": line.description,
            "quantity": line.quantity, "unit_price": line.unit_price, "discount_percent": line.discount_percent,
            "tax_rate": line.tax_rate, "subtotal": line.subtotal, "tax_amount": line.tax_amount, "total": line.total,
        } for line in invoice.lines],
    )


@router.get("/sales/customers", response_model=list[CustomerRead], tags=["sales"])
def list_sales_customers(
    q: str | None = Query(default=None, max_length=160), active: bool | None = None,
    limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0),
    context: AccessContext = Depends(require_permission("sales:read")), session: Session = Depends(get_db),
):
    return sales_service.list_customers(session, context.membership.company_id, limit, offset, q, active)


@router.get("/sales/availability", response_model=list[StockAvailabilityRead], tags=["sales"])
def sales_stock_availability(
    warehouse_id: int | None = Query(default=None, gt=0), product_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=100, ge=1, le=200), offset: int = Query(default=0, ge=0),
    context: AccessContext = Depends(require_permission("sales:read")), session: Session = Depends(get_db),
):
    if warehouse_id is not None and session.scalar(select(Warehouse.id).where(Warehouse.id == warehouse_id, Warehouse.company_id == context.membership.company_id)) is None:
        raise HTTPException(status_code=404, detail="Warehouse not found")
    if product_id is not None and session.scalar(select(Product.id).where(Product.id == product_id, Product.company_id == context.membership.company_id)) is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return sales_service.list_availability(session, context.membership.company_id, warehouse_id, product_id, limit, offset)


@router.post("/sales/customers", response_model=CustomerRead, status_code=201, tags=["sales"])
def create_sales_customer(data: CustomerCreate, request: Request, context: AccessContext = Depends(require_permission("sales:manage")), session: Session = Depends(get_db)):
    try:
        return sales_service.create_customer(session, context.membership.company_id, data, context.user.id, request.client.host if request.client else None)
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Customer code already exists in this company") from exc


@router.get("/sales/customers/{customer_id}", response_model=CustomerRead, tags=["sales"])
def get_sales_customer(customer_id: int, context: AccessContext = Depends(require_permission("sales:read")), session: Session = Depends(get_db)):
    customer = sales_service.get_customer(session, context.membership.company_id, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


@router.patch("/sales/customers/{customer_id}", response_model=CustomerRead, tags=["sales"])
def update_sales_customer(customer_id: int, data: CustomerUpdate, request: Request, context: AccessContext = Depends(require_permission("sales:manage")), session: Session = Depends(get_db)):
    customer = sales_service.update_customer(session, context.membership.company_id, customer_id, data, context.user.id, request.client.host if request.client else None)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


@router.get("/sales/quotes", response_model=list[QuoteRead], tags=["sales"])
def list_sales_quotes(
    status_filter: str | None = Query(default=None, alias="status", pattern=r"^(draft|sent|accepted|rejected|expired|converted)$"),
    customer_id: int | None = Query(default=None, gt=0), limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0),
    context: AccessContext = Depends(require_permission("sales:read")), session: Session = Depends(get_db),
):
    return sales_service.list_quotes(session, context.membership.company_id, limit, offset, status_filter, customer_id)


@router.post("/sales/quotes", response_model=QuoteRead, status_code=201, tags=["sales"])
def create_sales_quote(data: QuoteCreate, request: Request, context: AccessContext = Depends(require_permission("sales:manage")), session: Session = Depends(get_db)):
    try:
        return sales_service.create_quote(session, context.membership.company_id, data, context.user.id, request.client.host if request.client else None)
    except SalesError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Quote number already exists in this company") from exc


@router.get("/sales/quotes/{quote_id}", response_model=QuoteRead, tags=["sales"])
def get_sales_quote(quote_id: int, context: AccessContext = Depends(require_permission("sales:read")), session: Session = Depends(get_db)):
    quote = sales_service.get_quote(session, context.membership.company_id, quote_id)
    if quote is None:
        raise HTTPException(status_code=404, detail="Quote not found")
    return quote


@router.patch("/sales/quotes/{quote_id}", response_model=QuoteRead, tags=["sales"])
def update_sales_quote(quote_id: int, data: QuoteUpdate, request: Request, context: AccessContext = Depends(require_permission("sales:manage")), session: Session = Depends(get_db)):
    try:
        quote = sales_service.update_quote(session, context.membership.company_id, quote_id, data, context.user.id, request.client.host if request.client else None)
    except SalesError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if quote is None:
        raise HTTPException(status_code=404, detail="Quote not found")
    return quote


def _sales_quote_action(quote_id: int, action: str, request: Request, context: AccessContext, session: Session):
    try:
        quote = sales_service.transition_quote(session, context.membership.company_id, quote_id, action, context.user.id, request.client.host if request.client else None)
    except SalesError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if quote is None:
        raise HTTPException(status_code=404, detail="Quote not found")
    return quote


@router.post("/sales/quotes/{quote_id}/send", response_model=QuoteRead, tags=["sales"])
def send_sales_quote(quote_id: int, request: Request, context: AccessContext = Depends(require_permission("sales:manage")), session: Session = Depends(get_db)):
    return _sales_quote_action(quote_id, "send", request, context, session)


@router.post("/sales/quotes/{quote_id}/accept", response_model=QuoteRead, tags=["sales"])
def accept_sales_quote(quote_id: int, request: Request, context: AccessContext = Depends(require_permission("sales:manage")), session: Session = Depends(get_db)):
    return _sales_quote_action(quote_id, "accept", request, context, session)


@router.post("/sales/quotes/{quote_id}/reject", response_model=QuoteRead, tags=["sales"])
def reject_sales_quote(quote_id: int, request: Request, context: AccessContext = Depends(require_permission("sales:manage")), session: Session = Depends(get_db)):
    return _sales_quote_action(quote_id, "reject", request, context, session)


@router.post("/sales/quotes/{quote_id}/convert", response_model=OrderRead, status_code=201, tags=["sales"])
def convert_sales_quote(quote_id: int, data: QuoteConvert, request: Request, context: AccessContext = Depends(require_permission("sales:manage")), session: Session = Depends(get_db)):
    try:
        order = sales_service.convert_quote(session, context.membership.company_id, quote_id, data.number, data.warehouse_id, data.branch_id, context.user.id, request.client.host if request.client else None)
    except SalesError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Quote was already converted or order number already exists") from exc
    if order is None:
        raise HTTPException(status_code=404, detail="Quote not found")
    return order


@router.get("/sales/quotes/{quote_id}/events", response_model=list[dict], tags=["sales"])
def sales_quote_events(quote_id: int, context: AccessContext = Depends(require_permission("sales:read")), session: Session = Depends(get_db)):
    if session.scalar(select(Quote.id).where(Quote.id == quote_id, Quote.company_id == context.membership.company_id)) is None:
        raise HTTPException(status_code=404, detail="Quote not found")
    return session.scalars(select(SalesEvent).where(SalesEvent.quote_id == quote_id, SalesEvent.company_id == context.membership.company_id).order_by(SalesEvent.id)).all()


@router.get("/sales/orders/{order_id}/events", response_model=list[dict], tags=["sales"])
def sales_order_events(order_id: int, context: AccessContext = Depends(require_permission("sales:read")), session: Session = Depends(get_db)):
    if session.scalar(select(SalesOrder.id).where(SalesOrder.id == order_id, SalesOrder.company_id == context.membership.company_id)) is None:
        raise HTTPException(status_code=404, detail="Sales order not found")
    return session.scalars(select(SalesEvent).where(SalesEvent.order_id == order_id, SalesEvent.company_id == context.membership.company_id).order_by(SalesEvent.id)).all()


@router.get("/sales/orders", response_model=list[OrderRead], tags=["sales"])
def list_sales_orders(
    status_filter: str | None = Query(default=None, alias="status", pattern=r"^(draft|confirmed|partially_fulfilled|fulfilled|cancelled)$"),
    customer_id: int | None = Query(default=None, gt=0), limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0),
    context: AccessContext = Depends(require_permission("sales:read")), session: Session = Depends(get_db),
):
    return sales_service.list_orders(session, context.membership.company_id, limit, offset, status_filter, customer_id)


@router.post("/sales/orders", response_model=OrderRead, status_code=201, tags=["sales"])
def create_sales_order(data: OrderCreate, request: Request, context: AccessContext = Depends(require_permission("sales:manage")), session: Session = Depends(get_db)):
    try:
        return sales_service.create_order(session, context.membership.company_id, data, context.user.id, request.client.host if request.client else None)
    except SalesError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Sales order number already exists in this company") from exc


@router.get("/sales/orders/{order_id}", response_model=OrderRead, tags=["sales"])
def get_sales_order(order_id: int, context: AccessContext = Depends(require_permission("sales:read")), session: Session = Depends(get_db)):
    order = sales_service.get_order(session, context.membership.company_id, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Sales order not found")
    return order


def _sales_order_action(order_id: int, action: str, request: Request, context: AccessContext, session: Session):
    try:
        order = sales_service.transition_order(session, context.membership.company_id, order_id, action, context.user.id, request.client.host if request.client else None)
    except SalesError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if order is None:
        raise HTTPException(status_code=404, detail="Sales order not found")
    return order


@router.post("/sales/orders/{order_id}/confirm", response_model=OrderRead, tags=["sales"])
def confirm_sales_order(order_id: int, request: Request, context: AccessContext = Depends(require_permission("sales:manage")), session: Session = Depends(get_db)):
    return _sales_order_action(order_id, "confirm", request, context, session)


@router.post("/sales/orders/{order_id}/cancel", response_model=OrderRead, tags=["sales"])
def cancel_sales_order(order_id: int, request: Request, context: AccessContext = Depends(require_permission("sales:manage")), session: Session = Depends(get_db)):
    return _sales_order_action(order_id, "cancel", request, context, session)


@router.post("/sales/orders/{order_id}/deliveries", response_model=DeliveryRead, status_code=201, tags=["sales"])
def deliver_sales_order(order_id: int, data: DeliveryCreate, request: Request, idempotency_key: str = Header(alias="Idempotency-Key"), context: AccessContext = Depends(require_permission("sales:manage")), session: Session = Depends(get_db)):
    try:
        return sales_service.create_delivery(session, context.membership.company_id, order_id, data, idempotency_key, context.user.id, request.client.host if request.client else None)
    except (SalesError, InventoryError) as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Delivery conflicts with inventory or order state") from exc


@router.get("/sales/orders/{order_id}/deliveries", response_model=list[DeliveryRead], tags=["sales"])
def list_sales_deliveries(order_id: int, limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0), context: AccessContext = Depends(require_permission("sales:read")), session: Session = Depends(get_db)):
    if session.scalar(select(SalesOrder.id).where(SalesOrder.id == order_id, SalesOrder.company_id == context.membership.company_id)) is None:
        raise HTTPException(status_code=404, detail="Sales order not found")
    rows = session.scalars(select(SalesDelivery).where(SalesDelivery.order_id == order_id, SalesDelivery.company_id == context.membership.company_id)
        .options(joinedload(SalesDelivery.lines)).order_by(SalesDelivery.id.desc()).offset(offset).limit(limit)).unique().all()
    return [DeliveryRead(id=row.id, order_id=row.order_id, warehouse_id=row.warehouse_id, notes=row.notes, created_at=row.created_at,
        lines=[DeliveryLineRead.model_validate(line) for line in row.lines]) for row in rows]


@router.get("/sales/deliveries/{delivery_id}", response_model=DeliveryRead, tags=["sales"])
def get_sales_delivery(delivery_id: int, context: AccessContext = Depends(require_permission("sales:read")), session: Session = Depends(get_db)):
    delivery = sales_service.get_delivery(session, context.membership.company_id, delivery_id)
    if delivery is None:
        raise HTTPException(status_code=404, detail="Sales delivery not found")
    return DeliveryRead(id=delivery.id, order_id=delivery.order_id, warehouse_id=delivery.warehouse_id,
        notes=delivery.notes, created_at=delivery.created_at, lines=[DeliveryLineRead.model_validate(line) for line in delivery.lines])


@router.post("/sales/returns", response_model=ReturnRead, status_code=201, tags=["sales"])
def create_sales_return(data: ReturnCreate, request: Request, idempotency_key: str = Header(alias="Idempotency-Key"), context: AccessContext = Depends(require_permission("sales:manage")), session: Session = Depends(get_db)):
    try:
        return sales_service.create_return(session, context.membership.company_id, data, idempotency_key, context.user.id, request.client.host if request.client else None)
    except (SalesError, InventoryError) as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Sales return conflicts with inventory or invoice state") from exc


@router.get("/sales/returns/{return_id}", response_model=ReturnRead, tags=["sales"])
def get_sales_return(return_id: int, context: AccessContext = Depends(require_permission("sales:read")), session: Session = Depends(get_db)):
    row = session.scalar(select(SalesReturn).where(SalesReturn.id == return_id, SalesReturn.company_id == context.membership.company_id))
    if row is None:
        raise HTTPException(status_code=404, detail="Sales return not found")
    return row


@router.get("/sales/invoices", response_model=list[InvoiceRead], tags=["sales"])
def list_sales_invoices(customer_id: int | None = Query(default=None, gt=0), limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0), context: AccessContext = Depends(require_permission("sales:read")), session: Session = Depends(get_db)):
    return [_sales_invoice_read(row) for row in sales_service.list_invoices(session, context.membership.company_id, limit, offset, customer_id)]


@router.post("/sales/invoices", response_model=InvoiceRead, status_code=201, tags=["sales"])
def create_sales_invoice(data: InvoiceCreate, request: Request, context: AccessContext = Depends(require_permission("sales:manage")), session: Session = Depends(get_db)):
    try:
        return _sales_invoice_read(sales_service.create_invoice(session, context.membership.company_id, data, context.user.id, request.client.host if request.client else None))
    except SalesError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Invoice number already exists or order billing changed") from exc


@router.get("/sales/invoices/{invoice_id}", response_model=InvoiceRead, tags=["sales"])
def get_sales_invoice(invoice_id: int, context: AccessContext = Depends(require_permission("sales:read")), session: Session = Depends(get_db)):
    invoice = sales_service.get_invoice(session, context.membership.company_id, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail="Sales invoice not found")
    return _sales_invoice_read(invoice)


@router.post("/sales/invoices/{invoice_id}/void", response_model=InvoiceRead, tags=["sales"])
def void_sales_invoice(invoice_id: int, request: Request, context: AccessContext = Depends(require_permission("sales:manage")), session: Session = Depends(get_db)):
    try:
        invoice = sales_service.void_invoice(session, context.membership.company_id, invoice_id, context.user.id, request.client.host if request.client else None)
    except SalesError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if invoice is None:
        raise HTTPException(status_code=404, detail="Sales invoice not found")
    return _sales_invoice_read(invoice)


@router.post("/sales/invoices/{invoice_id}/payments", response_model=SalesPaymentRead, status_code=201, tags=["sales"])
def post_sales_payment(invoice_id: int, data: SalesPaymentCreate, request: Request, idempotency_key: str = Header(alias="Idempotency-Key"), context: AccessContext = Depends(require_permission("sales:manage")), session: Session = Depends(get_db)):
    try:
        return sales_service.post_payment(session, context.membership.company_id, invoice_id, data, idempotency_key, context.user.id, request.client.host if request.client else None)
    except SalesError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Payment conflicts with current invoice balance") from exc


@router.get("/sales/invoices/{invoice_id}/payments", response_model=list[SalesPaymentRead], tags=["sales"])
def list_sales_payments(invoice_id: int, context: AccessContext = Depends(require_permission("sales:read")), session: Session = Depends(get_db)):
    if session.scalar(select(SalesInvoice.id).where(SalesInvoice.id == invoice_id, SalesInvoice.company_id == context.membership.company_id)) is None:
        raise HTTPException(status_code=404, detail="Sales invoice not found")
    return session.scalars(select(SalesPayment).where(SalesPayment.invoice_id == invoice_id, SalesPayment.company_id == context.membership.company_id).order_by(SalesPayment.id)).all()


@router.get("/sales/credit-notes", response_model=list[dict], tags=["sales"])
def list_sales_credit_notes(limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0), context: AccessContext = Depends(require_permission("sales:read")), session: Session = Depends(get_db)):
    return session.scalars(select(SalesCreditNote).where(SalesCreditNote.company_id == context.membership.company_id)
        .order_by(SalesCreditNote.id.desc()).offset(offset).limit(limit)).all()
