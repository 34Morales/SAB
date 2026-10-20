"""Use cases for company-scoped product catalogs."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.modules.catalog.models import CatalogCategory, Product
from backend.app.modules.catalog.schemas import (
    CategoryCreate,
    CategoryUpdate,
    ProductCreate,
    ProductUpdate,
)
from backend.app.modules.identity.models import SecurityEvent


class CatalogCategoryNotFound(LookupError):
    pass


def list_categories(session: Session, company_id: int, limit: int, offset: int) -> list[CatalogCategory]:
    return list(session.scalars(
        select(CatalogCategory)
        .where(CatalogCategory.company_id == company_id)
        .order_by(CatalogCategory.name, CatalogCategory.id)
        .offset(offset)
        .limit(limit)
    ))


def create_category(
    session: Session, company_id: int, data: CategoryCreate, actor_user_id: int, source_ip: str | None
) -> CatalogCategory:
    category = CatalogCategory(company_id=company_id, **data.model_dump())
    session.add(category)
    session.flush()
    session.add(SecurityEvent(
        user_id=actor_user_id,
        company_id=company_id,
        event_type="catalog_category_created",
        source_ip=source_ip,
        details={"category_id": category.id},
    ))
    _commit(session)
    session.refresh(category)
    return category


def update_category(
    session: Session,
    company_id: int,
    category_id: int,
    data: CategoryUpdate,
    actor_user_id: int,
    source_ip: str | None,
) -> CatalogCategory | None:
    category = session.scalar(select(CatalogCategory).where(
        CatalogCategory.id == category_id, CatalogCategory.company_id == company_id,
    ))
    if category is None:
        return None
    changes = data.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(category, field, value)
    session.add(SecurityEvent(
        user_id=actor_user_id,
        company_id=company_id,
        event_type="catalog_category_deactivated" if changes.get("is_active") is False else "catalog_category_updated",
        source_ip=source_ip,
        details={"category_id": category.id, "changed_fields": sorted(changes)},
    ))
    _commit(session)
    session.refresh(category)
    return category


def list_products(
    session: Session,
    company_id: int,
    limit: int,
    offset: int,
    category_id: int | None = None,
    search: str | None = None,
) -> list[Product]:
    statement = select(Product).where(Product.company_id == company_id)
    if category_id is not None:
        statement = statement.where(Product.category_id == category_id)
    if search:
        pattern = f"%{search.strip()}%"
        statement = statement.where((Product.sku.ilike(pattern)) | (Product.name.ilike(pattern)))
    statement = statement.order_by(Product.id).offset(offset).limit(limit)
    return list(session.scalars(statement))


def create_product(
    session: Session, company_id: int, data: ProductCreate, actor_user_id: int, source_ip: str | None
) -> Product:
    values = data.model_dump()
    if values["category_id"] is not None:
        category = session.scalar(select(CatalogCategory).where(
            CatalogCategory.id == values["category_id"],
            CatalogCategory.company_id == company_id,
            CatalogCategory.is_active.is_(True),
        ))
        if category is None:
            raise CatalogCategoryNotFound("Category not found in active company")
    product = Product(company_id=company_id, **values)
    session.add(product)
    session.flush()
    session.add(SecurityEvent(
        user_id=actor_user_id,
        company_id=company_id,
        event_type="product_created",
        source_ip=source_ip,
        details={"product_id": product.id, "sku": product.sku},
    ))
    _commit(session)
    session.refresh(product)
    return product


def update_product(
    session: Session,
    company_id: int,
    product_id: int,
    data: ProductUpdate,
    actor_user_id: int,
    source_ip: str | None,
) -> Product | None:
    product = session.scalar(select(Product).where(Product.id == product_id, Product.company_id == company_id))
    if product is None:
        return None
    changes = data.model_dump(exclude_unset=True)
    if changes.get("category_id") is not None:
        category = session.scalar(select(CatalogCategory).where(
            CatalogCategory.id == changes["category_id"],
            CatalogCategory.company_id == company_id,
            CatalogCategory.is_active.is_(True),
        ))
        if category is None:
            raise CatalogCategoryNotFound("Category not found in active company")
    for field, value in changes.items():
        setattr(product, field, value)
    session.add(SecurityEvent(
        user_id=actor_user_id,
        company_id=company_id,
        event_type="product_deactivated" if changes.get("is_active") is False else "product_updated",
        source_ip=source_ip,
        details={"product_id": product.id, "changed_fields": sorted(changes)},
    ))
    _commit(session)
    session.refresh(product)
    return product


def _commit(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise
