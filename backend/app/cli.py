"""Interactive bootstrap for the first company administrator."""

from getpass import getpass

from sqlalchemy import select

from backend.app.database.session import SessionLocal
from backend.app.modules.companies.models import Company
from backend.app.modules.identity.models import Membership, Role, User
from backend.app.modules.identity.security import hash_password


def bootstrap_admin() -> None:
    legal_name = input("Company legal name: ").strip()
    email = input("Administrator email: ").strip().lower()
    full_name = input("Administrator name: ").strip()
    password = getpass("Administrator password (12+ characters): ")
    if len(password) < 12:
        raise SystemExit("Password must contain at least 12 characters")

    with SessionLocal.begin() as session:
        if session.scalar(select(User.id).where(User.email == email)):
            raise SystemExit("That email already exists")
        company = Company(legal_name=legal_name, country_code="MX")
        user = User(email=email, full_name=full_name, password_hash=hash_password(password))
        role = session.scalar(select(Role).where(Role.name == "admin", Role.company_id.is_(None)))
        if role is None:
            raise SystemExit("Run `alembic upgrade head` before bootstrapping")
        session.add(Membership(company=company, user=user, roles=[role]))


if __name__ == "__main__":
    bootstrap_admin()
