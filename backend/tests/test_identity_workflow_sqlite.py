"""Local database integration coverage for identity security workflows."""

import base64
import hashlib
import hmac
import struct
import time
from datetime import datetime, timedelta, timezone

import pytest
from alembic import command
from alembic.config import Config
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from cryptography.fernet import Fernet
from sqlalchemy import Boolean, Column, DateTime, Integer, MetaData, String, Table, create_engine, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from backend.app.core.config import get_settings
from backend.app.database.base import Base
from backend.app.modules.companies.models import Company
from backend.app.modules.identity.models import Membership, RecoveryEmailOutbox, Role, SecurityEvent, User
from backend.app.modules.identity.security import decrypt_secret, encrypt_secret, hash_password
from backend.app.modules.identity.service import (
    LoginFailure,
    authenticate,
    begin_mfa_setup,
    confirm_mfa_setup,
    request_password_recovery,
    reset_password,
    resolve_access,
)


@pytest.fixture
def identity_db(tmp_path, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("MFA_ENCRYPTION_KEY", key)
    get_settings.cache_clear()
    engine = create_engine(f"sqlite:///{tmp_path / 'identity.sqlite3'}", connect_args={"check_same_thread": False})
    with engine.connect() as connection:
        alembic_config = Config("backend/alembic.ini")
        alembic_config.attributes["connection"] = connection
        command.upgrade(alembic_config, "head")
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def test_migrations_adopt_legacy_company_tables_without_losing_rows(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.sqlite3'}")
    legacy_metadata = MetaData()
    legacy_companies = Table(
        "companies",
        legacy_metadata,
        Column("id", Integer, primary_key=True),
        Column("legal_name", String(200), nullable=False),
        Column("tax_id", String(20)),
        Column("country_code", String(2), nullable=False),
        Column("is_active", Boolean, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
    )
    legacy_metadata.create_all(bind=engine)
    company_id = 17
    with engine.begin() as connection:
        connection.execute(legacy_companies.insert().values(
            id=company_id,
            legal_name="Legacy company",
            country_code="MX",
            is_active=True,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        ))
    with engine.connect() as connection:
        alembic_config = Config("backend/alembic.ini")
        alembic_config.attributes["connection"] = connection
        command.upgrade(alembic_config, "head")
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    with sessionmaker(bind=engine)() as db:
        assert db.get(Company, company_id).legal_name == "Legacy company"
        assert db.scalar(select(Role).where(Role.name == "admin", Role.company_id.is_(None))) is not None
    engine.dispose()


def _totp(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{number:06d}"


def test_mfa_recovery_password_reset_throttle_and_append_only_audit(identity_db):
    with identity_db.begin() as db:
        company = Company(legal_name="Local security integration", country_code="MX")
        user = User(email="workflow@example.test", full_name="Workflow User", password_hash=hash_password("correct-test-password"))
        admin = db.scalar(select(Role).where(Role.name == "admin", Role.company_id.is_(None)))
        db.add(Membership(company=company, user=user, roles=[admin]))
        db.flush()
        custom_role = Role(company_id=company.id, name="sequence-check", is_system=False)
        db.add(custom_role)
        db.flush()
        assert custom_role.id > 0
        user_id, company_id, email = user.id, company.id, user.email

    with identity_db() as db:
        secret, _uri = begin_mfa_setup(db, db.get(User, user_id), company_id, "192.0.2.10")
    for _ in range(4):
        with identity_db() as db, pytest.raises(LoginFailure, match="invalid_credentials"):
            confirm_mfa_setup(db, user_id, company_id, "000000", "198.51.100.44")
    with identity_db() as db, pytest.raises(LoginFailure, match="rate_limited"):
        confirm_mfa_setup(db, user_id, company_id, "000000", "198.51.100.44")
    setup_step = int(time.time()) // 30 + 1
    with identity_db() as db:
        recovery_codes = confirm_mfa_setup(db, user_id, company_id, _totp(secret, setup_step), "192.0.2.10")
    assert len(recovery_codes) == 10

    with identity_db() as db, pytest.raises(LoginFailure, match="mfa_required"):
        authenticate(db, email, "correct-test-password", "192.0.2.11", company_id=company_id)
    with identity_db() as db, pytest.raises(LoginFailure, match="invalid_credentials"):
        authenticate(db, email, "correct-test-password", "192.0.2.11", otp_code=_totp(secret, setup_step), company_id=company_id)

    with identity_db() as db:
        token, _ = authenticate(db, email, "correct-test-password", "192.0.2.11", recovery_code=recovery_codes[0], company_id=company_id)
        assert resolve_access(db, token, company_id) is not None
        assert resolve_access(db, token, company_id + 1) is None
    with identity_db() as db, pytest.raises(LoginFailure, match="invalid_credentials"):
        authenticate(db, email, "correct-test-password", "192.0.2.14", recovery_code=recovery_codes[0], company_id=company_id)

    with identity_db() as db:
        request_password_recovery(db, email, "192.0.2.12")
        queued = db.scalar(select(RecoveryEmailOutbox).where(RecoveryEmailOutbox.recipient == email))
        reset_token = decrypt_secret(queued.encrypted_token)
        reset_password(db, reset_token, "new-correct-test-password", "192.0.2.12")
        with pytest.raises(ValueError, match="Invalid or expired"):
            reset_password(db, reset_token, "another-password-value", "192.0.2.12")
        assert resolve_access(db, token, company_id) is None

    with identity_db() as db:
        new_token, _ = authenticate(db, email, "new-correct-test-password", "192.0.2.13", recovery_code=recovery_codes[1], company_id=company_id)
        assert resolve_access(db, new_token, company_id) is not None

    for _ in range(4):
        with identity_db() as db, pytest.raises(LoginFailure, match="invalid_credentials"):
            authenticate(db, email, "incorrect-password", "198.51.100.40", company_id=company_id)
    with identity_db() as db, pytest.raises(LoginFailure, match="rate_limited"):
        authenticate(db, email, "incorrect-password", "198.51.100.40", company_id=company_id)

    with identity_db() as db:
        event = db.scalar(select(SecurityEvent).where(SecurityEvent.event_type == "mfa_enabled"))
        assert event is not None and event.company_id == company_id
        with pytest.raises(IntegrityError):
            db.execute(update(SecurityEvent).where(SecurityEvent.id == event.id).values(event_type="tampered"))
        db.rollback()


def test_recovery_mailer_sends_and_scrubs_outbox_secret(identity_db, monkeypatch):
    from backend.app.modules.identity import mailer

    monkeypatch.setenv("SMTP_HOST", "mail.example.test")
    monkeypatch.setenv("SMTP_FROM", "ERP <no-reply@example.test>")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USE_SSL", "false")
    get_settings.cache_clear()
    monkeypatch.setattr(mailer, "SessionLocal", identity_db)
    with identity_db.begin() as db:
        db.add(RecoveryEmailOutbox(
            recipient="mailer@example.test",
            encrypted_token=encrypt_secret("one-time-test-token"),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        ))

    class FakeSMTP:
        message = None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def starttls(self, **_kwargs):
            pass

        def send_message(self, message):
            self.message = message

    smtp = FakeSMTP()
    monkeypatch.setattr(mailer.smtplib, "SMTP", lambda *_args, **_kwargs: smtp)
    assert mailer.deliver_one()
    assert "one-time-test-token" in smtp.message.get_content()
    with identity_db() as db:
        item = db.scalar(select(RecoveryEmailOutbox).where(RecoveryEmailOutbox.id == 1))
        assert item.sent_at is not None
        assert item.recipient == ""
        assert item.encrypted_token == ""
