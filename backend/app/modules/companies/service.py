"""Company use cases."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.modules.companies.models import Branch, Company
from backend.app.modules.companies.schemas import CompanyCreate, CompanySettingsUpdate
from backend.app.modules.companies.branch_schemas import BranchCreate, BranchUpdate
from backend.app.modules.identity.models import SecurityEvent


def create_company(session: Session, data: CompanyCreate) -> Company:
    company = Company(**data.model_dump())
    session.add(company)
    session.commit()
    session.refresh(company)
    return company


def list_companies(session: Session, company_ids: list[int], limit: int, offset: int) -> list[Company]:
    statement = select(Company).where(Company.id.in_(company_ids)).order_by(Company.id).offset(offset).limit(limit)
    return list(session.scalars(statement))


def list_branches(session: Session, company_id: int, limit: int, offset: int) -> list[Branch]:
    statement = (
        select(Branch)
        .where(Branch.company_id == company_id)
        .order_by(Branch.id)
        .offset(offset)
        .limit(limit)
    )
    return list(session.scalars(statement))


def create_branch(
    session: Session, company_id: int, data: BranchCreate, actor_user_id: int, source_ip: str | None
) -> Branch:
    branch = Branch(company_id=company_id, **data.model_dump())
    session.add(branch)
    session.flush()
    session.add(SecurityEvent(
        user_id=actor_user_id,
        company_id=company_id,
        event_type="branch_created",
        source_ip=source_ip,
        details={"branch_id": branch.id, "code": branch.code},
    ))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise
    session.refresh(branch)
    return branch


def update_branch(
    session: Session, company_id: int, branch_id: int, data: BranchUpdate, actor_user_id: int, source_ip: str | None
) -> Branch | None:
    branch = session.scalar(select(Branch).where(Branch.id == branch_id, Branch.company_id == company_id))
    if branch is None:
        return None
    changes = data.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(branch, field, value)
    event_type = "branch_deactivated" if changes.get("is_active") is False else "branch_updated"
    session.add(SecurityEvent(
        user_id=actor_user_id,
        company_id=company_id,
        event_type=event_type,
        source_ip=source_ip,
        details={"branch_id": branch.id, "changed_fields": sorted(changes)},
    ))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise
    session.refresh(branch)
    return branch


def get_company(session: Session, company_id: int) -> Company | None:
    return session.get(Company, company_id)


def update_company_settings(
    session: Session,
    company_id: int,
    data: CompanySettingsUpdate,
    actor_user_id: int,
    source_ip: str | None,
) -> Company | None:
    company = session.get(Company, company_id)
    if company is None:
        return None
    changes = data.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(company, field, value)
    session.add(SecurityEvent(
        user_id=actor_user_id,
        company_id=company_id,
        event_type="company_settings_updated",
        source_ip=source_ip,
        details={"changed_fields": sorted(changes)},
    ))
    session.commit()
    session.refresh(company)
    return company
