"""Identity use cases and company-scoped authorization."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, delete, distinct, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from backend.app.modules.companies.models import Company
from backend.app.modules.identity.models import (
    AuthSession,
    LoginThrottle,
    Membership,
    Permission,
    MfaRecoveryCode,
    PasswordRecoveryToken,
    RecoveryEmailOutbox,
    Role,
    SecurityEvent,
    User,
    membership_roles,
    role_permissions,
)
from backend.app.modules.identity.schemas import RoleCreate, RoleUpdate, UserCreate
from backend.app.modules.identity.security import (
    decrypt_secret,
    encrypt_secret,
    hash_password,
    issue_token,
    new_recovery_codes,
    new_totp_secret,
    subject_digest,
    token_digest,
    totp_uri,
    verify_password,
    verify_totp,
)

SESSION_LIFETIME = timedelta(hours=8)
THROTTLE_WINDOW = timedelta(minutes=15)
THROTTLE_LOCK = timedelta(minutes=15)
RECOVERY_LIFETIME = timedelta(minutes=20)
_DUMMY_PASSWORD_HASH = hash_password("not-a-real-user-account-password")


class LoginFailure(Exception):
    def __init__(self, reason: str, retry_after: int = 0):
        self.reason = reason
        self.retry_after = retry_after


def _audit(
    session: Session,
    event_type: str,
    source_ip: str | None,
    *,
    user_id: int | None = None,
    company_id: int | None = None,
    subject: str | None = None,
    details: dict[str, object] | None = None,
) -> None:
    session.add(
        SecurityEvent(
            user_id=user_id,
            company_id=company_id,
            event_type=event_type,
            source_ip=(source_ip or "")[:64] or None,
            subject_digest=subject_digest(subject) if subject else None,
            details=details or {},
        )
    )


def _throttle_keys(email: str, source_ip: str) -> tuple[tuple[str, int], tuple[str, int]]:
    ip = source_ip or "unknown"
    return (
        (subject_digest(f"login-ip|{ip}"), 30),
        (subject_digest(f"login-account|{email.lower()}|{ip}"), 5),
    )


def _blocked_until(session: Session, keys: tuple[tuple[str, int], ...], now: datetime) -> datetime | None:
    digests = [key for key, _ in keys]
    rows = session.scalars(
        select(LoginThrottle).where(LoginThrottle.key_digest.in_(digests), LoginThrottle.blocked_until > now)
    ).all()
    blocked = [row.blocked_until.replace(tzinfo=timezone.utc) if row.blocked_until and row.blocked_until.tzinfo is None else row.blocked_until for row in rows]
    return max((value for value in blocked if value), default=None)


def _increment_throttle(session: Session, key: str, limit: int, now: datetime) -> None:
    dialect = session.get_bind().dialect.name
    insert = pg_insert if dialect == "postgresql" else sqlite_insert if dialect == "sqlite" else None
    if insert is None:
        raise RuntimeError(f"Login throttling is unsupported for database dialect {dialect}")
    fresh = LoginThrottle.window_started_at <= now - THROTTLE_WINDOW
    failures = case((fresh, 1), else_=LoginThrottle.failure_count + 1)
    statement = insert(LoginThrottle).values(
        key_digest=key, window_started_at=now, failure_count=1, blocked_until=None
    )
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[LoginThrottle.key_digest],
            set_={
                "window_started_at": case((fresh, now), else_=LoginThrottle.window_started_at),
                "failure_count": failures,
                "blocked_until": case((failures >= limit, now + THROTTLE_LOCK), else_=None),
            },
        )
    )


def _record_login_failure(
    session: Session, email: str, source_ip: str | None, now: datetime, user_id: int | None, event: str,
    company_id: int | None = None,
) -> None:
    keys = _throttle_keys(email, source_ip or "unknown")
    for key, limit in keys:
        _increment_throttle(session, key, limit, now)
    _audit(session, event, source_ip, user_id=user_id, company_id=company_id, subject=email, details={"outcome": "denied"})
    blocked = _blocked_until(session, keys, now)
    session.commit()
    if blocked:
        seconds = max(1, int((blocked - now).total_seconds()))
        raise LoginFailure("rate_limited", seconds)
    raise LoginFailure("invalid_credentials")


def _enforce_factor_throttle(
    session: Session, user: User, source_ip: str | None, now: datetime, company_id: int
) -> tuple[tuple[str, int], ...]:
    keys = _throttle_keys(user.email, source_ip or "unknown")
    blocked = _blocked_until(session, keys, now)
    if blocked:
        _audit(session, "identity_factor_throttled", source_ip, user_id=user.id, company_id=company_id)
        session.commit()
        raise LoginFailure("rate_limited", max(1, int((blocked - now).total_seconds())))
    return keys


def _record_factor_failure(
    session: Session, user: User, source_ip: str | None, now: datetime, event: str, company_id: int
) -> None:
    _record_login_failure(session, user.email, source_ip, now, user.id, event, company_id)


@dataclass(frozen=True)
class AccessContext:
    user: User
    membership: Membership


def authenticate(
    session: Session,
    email: str,
    password: str,
    source_ip: str | None = None,
    otp_code: str | None = None,
    recovery_code: str | None = None,
    company_id: int | None = None,
) -> tuple[str, int]:
    normalized_email = email.strip().lower()
    now = datetime.now(timezone.utc)
    keys = _throttle_keys(normalized_email, source_ip or "unknown")
    user = session.scalar(select(User).where(User.email == normalized_email))
    blocked = _blocked_until(session, keys, now)
    if blocked:
        seconds = max(1, int((blocked - now).total_seconds()))
        _audit(session, "login_throttled", source_ip, user_id=user.id if user else None, company_id=company_id, subject=normalized_email)
        session.commit()
        raise LoginFailure("rate_limited", seconds)
    password_ok = verify_password(password, user.password_hash if user else _DUMMY_PASSWORD_HASH)
    if user is None or not user.is_active or not password_ok:
        _record_login_failure(session, normalized_email, source_ip, now, user.id if user else None, "login_failed", company_id)

    assert user is not None
    if user.mfa_enabled:
        if not otp_code and not recovery_code:
            _audit(session, "mfa_challenge_issued", source_ip, user_id=user.id, company_id=company_id)
            session.commit()
            raise LoginFailure("mfa_required")
        factor_ok = False
        if otp_code and user.mfa_secret_encrypted:
            locked_user = session.scalar(
                select(User).where(User.id == user.id).with_for_update().execution_options(populate_existing=True)
            )
            secret = decrypt_secret(locked_user.mfa_secret_encrypted) if locked_user and locked_user.mfa_secret_encrypted else ""
            step = verify_totp(secret, otp_code, last_step=locked_user.mfa_last_used_step if locked_user else None)
            if step is not None and locked_user is not None:
                locked_user.mfa_last_used_step = step
                factor_ok = True
        elif recovery_code:
            digest = token_digest(recovery_code.replace("-", "").strip().upper())
            code = session.scalar(
                select(MfaRecoveryCode).where(
                    MfaRecoveryCode.user_id == user.id,
                    MfaRecoveryCode.code_digest == digest,
                    MfaRecoveryCode.consumed_at.is_(None),
                ).with_for_update()
            )
            if code:
                code.consumed_at = now
                factor_ok = True
        if not factor_ok:
            _record_login_failure(session, normalized_email, source_ip, now, user.id, "mfa_failed", company_id)

    if company_id is not None:
        member = session.scalar(
            select(Membership.id)
            .join(Company, Company.id == Membership.company_id)
            .where(Membership.user_id == user.id, Membership.company_id == company_id,
                   Membership.is_active.is_(True), Company.is_active.is_(True))
        )
        if member is None:
            _record_login_failure(session, normalized_email, source_ip, now, user.id, "login_company_denied", company_id)

    for key, _limit in keys:
        if key == keys[1][0]:
            session.execute(delete(LoginThrottle).where(LoginThrottle.key_digest == key))
    token = issue_token()
    session.add(AuthSession(user_id=user.id, token_digest=token_digest(token), expires_at=now + SESSION_LIFETIME))
    _audit(session, "login_succeeded", source_ip, user_id=user.id, company_id=company_id, subject=normalized_email)
    session.commit()
    return token, int(SESSION_LIFETIME.total_seconds())


def begin_mfa_setup(session: Session, user: User, company_id: int, source_ip: str | None) -> tuple[str, str]:
    if user.mfa_enabled:
        raise LookupError("MFA is already enabled")
    secret = new_totp_secret()
    user.mfa_pending_secret_encrypted = encrypt_secret(secret)
    _audit(session, "mfa_setup_started", source_ip, user_id=user.id, company_id=company_id)
    session.commit()
    return secret, totp_uri(secret, user.email)


def confirm_mfa_setup(session: Session, user_id: int, company_id: int, code: str, source_ip: str | None) -> list[str]:
    user = session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None or not user.mfa_pending_secret_encrypted:
        raise LookupError("MFA setup has not been started")
    if user.mfa_enabled:
        raise LookupError("MFA is already enabled")
    now = datetime.now(timezone.utc)
    keys = _enforce_factor_throttle(session, user, source_ip, now, company_id)
    secret = decrypt_secret(user.mfa_pending_secret_encrypted)
    step = verify_totp(secret, code, last_step=user.mfa_last_used_step)
    if step is None:
        _record_factor_failure(session, user, source_ip, now, "mfa_setup_failed", company_id)
    codes = new_recovery_codes()
    user.mfa_secret_encrypted = user.mfa_pending_secret_encrypted
    user.mfa_pending_secret_encrypted = None
    user.mfa_enabled = True
    user.mfa_last_used_step = step
    session.add_all([MfaRecoveryCode(user_id=user_id, code_digest=token_digest(code)) for code in codes])
    _audit(session, "mfa_enabled", source_ip, user_id=user_id, company_id=company_id)
    session.execute(delete(LoginThrottle).where(LoginThrottle.key_digest == keys[1][0]))
    session.commit()
    return codes


def disable_mfa(
    session: Session, user_id: int, company_id: int, password: str, otp_code: str | None, recovery_code: str | None, source_ip: str | None
) -> None:
    user = session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None or not user.mfa_enabled:
        _audit(session, "mfa_disable_failed", source_ip, user_id=user_id, company_id=company_id)
        session.commit()
        raise ValueError("Invalid credentials")
    now = datetime.now(timezone.utc)
    keys = _enforce_factor_throttle(session, user, source_ip, now, company_id)
    if not verify_password(password, user.password_hash):
        _record_factor_failure(session, user, source_ip, now, "mfa_disable_failed", company_id)
    accepted = False
    if otp_code and user.mfa_secret_encrypted:
        step = verify_totp(decrypt_secret(user.mfa_secret_encrypted), otp_code, last_step=user.mfa_last_used_step)
        if step is not None:
            user.mfa_last_used_step = step
            accepted = True
    elif recovery_code:
        digest = token_digest(recovery_code.replace("-", "").strip().upper())
        code = session.scalar(select(MfaRecoveryCode).where(
            MfaRecoveryCode.user_id == user_id,
            MfaRecoveryCode.code_digest == digest,
            MfaRecoveryCode.consumed_at.is_(None),
        ).with_for_update())
        if code:
            code.consumed_at = now
            accepted = True
    if not accepted:
        _record_factor_failure(session, user, source_ip, now, "mfa_disable_failed", company_id)
    user.mfa_enabled = False
    user.mfa_secret_encrypted = None
    user.mfa_pending_secret_encrypted = None
    user.mfa_last_used_step = None
    session.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_id))
    _audit(session, "mfa_disabled", source_ip, user_id=user_id, company_id=company_id)
    session.execute(delete(LoginThrottle).where(LoginThrottle.key_digest == keys[1][0]))
    session.commit()


def request_password_recovery(session: Session, email: str, source_ip: str | None) -> None:
    normalized = email.strip().lower()
    # Fail closed consistently when encryption is not provisioned, without email enumeration.
    try:
        encrypt_secret("configuration-check")
    except RuntimeError as exc:
        raise RuntimeError("Recovery encryption is not configured") from exc
    now = datetime.now(timezone.utc)
    session.execute(delete(PasswordRecoveryToken).where(PasswordRecoveryToken.expires_at <= now))
    session.execute(
        update(RecoveryEmailOutbox)
        .where(RecoveryEmailOutbox.expires_at <= now, RecoveryEmailOutbox.sent_at.is_(None))
        .values(recipient="", encrypted_token="", last_error="expired", claimed_at=None)
    )
    ip = source_ip or "unknown"
    recovery_keys = (
        (subject_digest(f"recovery-ip|{ip}"), 30),
        (subject_digest(f"recovery-account|{normalized}|{ip}"), 5),
    )
    blocked = _blocked_until(session, recovery_keys, now)
    if blocked:
        _audit(session, "password_recovery_throttled", source_ip, subject=normalized)
        session.commit()
        return
    for key, limit in recovery_keys:
        _increment_throttle(session, key, limit, now)
    blocked = _blocked_until(session, recovery_keys, now)
    user = session.scalar(select(User).where(User.email == normalized, User.is_active.is_(True)))
    _audit(
        session,
        "password_recovery_throttled" if blocked else "password_recovery_requested",
        source_ip,
        user_id=user.id if user else None,
        subject=normalized,
    )
    if user and not blocked:
        session.execute(
            update(PasswordRecoveryToken)
            .where(PasswordRecoveryToken.user_id == user.id, PasswordRecoveryToken.consumed_at.is_(None))
            .values(consumed_at=now)
        )
        session.execute(
            update(RecoveryEmailOutbox)
            .where(RecoveryEmailOutbox.recipient == user.email, RecoveryEmailOutbox.sent_at.is_(None))
            .values(recipient="", encrypted_token="", last_error="superseded", claimed_at=None)
        )
        token = issue_token()
        expires = now + RECOVERY_LIFETIME
        session.add(PasswordRecoveryToken(user_id=user.id, token_digest=token_digest(token), expires_at=expires))
        session.add(RecoveryEmailOutbox(
            recipient=user.email,
            encrypted_token=encrypt_secret(token),
            expires_at=expires,
        ))
    session.commit()


def reset_password(session: Session, token: str, new_password: str, source_ip: str | None) -> None:
    now = datetime.now(timezone.utc)
    reset = session.scalar(
        select(PasswordRecoveryToken)
        .where(
            PasswordRecoveryToken.token_digest == token_digest(token),
            PasswordRecoveryToken.consumed_at.is_(None),
            PasswordRecoveryToken.expires_at > now,
        )
        .with_for_update()
    )
    if reset is None:
        _audit(session, "password_recovery_failed", source_ip)
        session.commit()
        raise ValueError("Invalid or expired recovery token")
    consumed = session.execute(
        update(PasswordRecoveryToken)
        .where(PasswordRecoveryToken.id == reset.id, PasswordRecoveryToken.consumed_at.is_(None))
        .values(consumed_at=now)
    )
    if consumed.rowcount != 1:
        session.rollback()
        raise ValueError("Invalid or expired recovery token")
    session.execute(update(User).where(User.id == reset.user_id).values(password_hash=hash_password(new_password)))
    session.execute(update(AuthSession).where(AuthSession.user_id == reset.user_id, AuthSession.revoked_at.is_(None)).values(revoked_at=now))
    _audit(session, "password_recovery_succeeded", source_ip, user_id=reset.user_id)
    session.commit()


def resolve_access(session: Session, token: str, company_id: int) -> AccessContext | None:
    now = datetime.now(timezone.utc)
    auth_session = session.scalar(
        select(AuthSession)
        .options(joinedload(AuthSession.user))
        .where(
            AuthSession.token_digest == token_digest(token),
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > now,
        )
    )
    if auth_session is None or not auth_session.user.is_active:
        return None
    membership = session.scalars(
        select(Membership)
        .options(joinedload(Membership.roles).joinedload(Role.permissions))
        .where(
            Membership.user_id == auth_session.user_id,
            Membership.company_id == company_id,
            Membership.is_active.is_(True),
            Membership.company.has(Company.is_active.is_(True)),
        )
    ).unique().first()
    if membership is None:
        return None
    return AccessContext(auth_session.user, membership)


def has_permission(context: AccessContext, code: str) -> bool:
    return any(permission.code == code for role in context.membership.roles for permission in role.permissions)


def create_member(session: Session, company_id: int, data: UserCreate, actor_user_id: int | None = None, source_ip: str | None = None) -> User:
    role = session.scalar(select(Role).where(Role.id == data.role_id, Role.company_id == company_id))
    if role is None:
        role = session.scalar(select(Role).where(Role.id == data.role_id, Role.company_id.is_(None)))
    if role is None:
        raise LookupError("Role not found")
    user = User(email=data.email.lower(), full_name=data.full_name.strip(), password_hash=hash_password(data.password))
    membership = Membership(company_id=company_id, user=user, roles=[role])
    session.add(membership)
    session.flush()
    _audit(session, "user_created", source_ip, user_id=actor_user_id, company_id=company_id, subject=user.email,
           details={"created_user_id": user.id, "role_id": role.id})
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise
    session.refresh(user)
    return user


def update_membership(
    session: Session, company_id: int, membership_id: int, changes: dict[str, object],
    actor_user_id: int, source_ip: str | None,
) -> Membership | None:
    # Serialize administrator-count changes company-wide to avoid concurrent last-admin removal.
    session.scalar(select(Company.id).where(Company.id == company_id).with_for_update())
    membership = session.scalar(
        select(Membership).where(Membership.id == membership_id, Membership.company_id == company_id)
        .options(joinedload(Membership.roles).joinedload(Role.permissions)).with_for_update()
    )
    if membership is None:
        return None
    if not changes or any(value is None for value in changes.values()):
        raise ValueError("Membership update must include non-null fields")
    new_roles = None
    if "role_ids" in changes:
        role_ids = list(changes["role_ids"])
        new_roles = list(session.scalars(select(Role).where(
            Role.id.in_(role_ids), (Role.company_id == company_id) | Role.company_id.is_(None),
        ).options(joinedload(Role.permissions))).unique()) if role_ids else []
        if len(new_roles) != len(role_ids):
            raise LookupError("One or more roles are not available in this company")
    active_after = bool(changes.get("is_active", membership.is_active))
    roles_after = new_roles if new_roles is not None else membership.roles
    remains_admin = active_after and any(permission.code == "identity:manage" for role in roles_after for permission in role.permissions)
    if not remains_admin:
        other_admins = session.scalar(
            select(func.count(distinct(Membership.id)))
            .join(membership_roles, membership_roles.c.membership_id == Membership.id)
            .join(Role, Role.id == membership_roles.c.role_id)
            .join(role_permissions, role_permissions.c.role_id == Role.id)
            .join(Permission, Permission.id == role_permissions.c.permission_id)
            .where(Membership.company_id == company_id, Membership.is_active.is_(True),
                   Membership.id != membership_id, Permission.code == "identity:manage")
        ) or 0
        if other_admins == 0:
            raise ValueError("The company must retain at least one active identity administrator")
    if new_roles is not None:
        membership.roles = new_roles
    if "is_active" in changes:
        membership.is_active = bool(changes["is_active"])
    _audit(session, "membership_updated", source_ip, user_id=actor_user_id, company_id=company_id,
           subject=str(membership.user_id), details={"membership_id": membership.id,
           "changed_fields": sorted(changes), "active": membership.is_active,
           "role_ids": [role.id for role in membership.roles]})
    session.commit()
    session.refresh(membership)
    return membership


def create_custom_role(
    session: Session, company_id: int, data: RoleCreate, actor_user_id: int, source_ip: str | None,
) -> Role:
    permission_codes = set(data.permissions)
    permissions = list(session.scalars(select(Permission).where(Permission.code.in_(permission_codes))))
    if {permission.code for permission in permissions} != permission_codes:
        raise LookupError("One or more permissions are unavailable")
    role = Role(company_id=company_id, name=data.name, is_system=False, permissions=permissions)
    session.add(role)
    session.flush()
    _audit(session, "identity_role_created", source_ip, user_id=actor_user_id, company_id=company_id,
           subject=role.name, details={"role_id": role.id, "permissions": sorted(permission_codes)})
    session.commit()
    session.refresh(role)
    return role


def update_custom_role(
    session: Session, company_id: int, role_id: int, data: RoleUpdate,
    actor_user_id: int, source_ip: str | None,
) -> Role | None:
    session.scalar(select(Company.id).where(Company.id == company_id).with_for_update())
    role = session.scalar(select(Role).where(Role.id == role_id, Role.company_id == company_id).options(joinedload(Role.permissions)).with_for_update())
    if role is None:
        return None
    changes = data.model_dump(exclude_unset=True)
    if not changes:
        raise ValueError("At least one role field must be supplied")
    if "permissions" in changes:
        permission_codes = set(changes["permissions"])
        currently_admin = any(permission.code == "identity:manage" for permission in role.permissions)
        assigned_to_active_member = session.scalar(
            select(Membership.id).join(membership_roles, membership_roles.c.membership_id == Membership.id)
            .where(Membership.company_id == company_id, Membership.is_active.is_(True), membership_roles.c.role_id == role.id)
            .limit(1)
        ) is not None
        if currently_admin and assigned_to_active_member and "identity:manage" not in permission_codes:
            raise ValueError("Reassign active members before removing identity administration from this role")
        permissions = list(session.scalars(select(Permission).where(Permission.code.in_(permission_codes))))
        if {permission.code for permission in permissions} != permission_codes:
            raise LookupError("One or more permissions are unavailable")
        role.permissions = permissions
    if "name" in changes:
        role.name = changes["name"]
    _audit(session, "identity_role_updated", source_ip, user_id=actor_user_id, company_id=company_id,
           subject=role.name, details={"role_id": role.id, "changed_fields": sorted(changes),
           "permissions": sorted(permission.code for permission in role.permissions)})
    session.commit()
    session.refresh(role)
    return role


def delete_custom_role(
    session: Session, company_id: int, role_id: int,
    actor_user_id: int, source_ip: str | None,
) -> bool | None:
    """Delete an unused tenant role; preserve system and assigned roles."""
    session.scalar(select(Company.id).where(Company.id == company_id).with_for_update())
    role = session.scalar(select(Role).where(
        Role.id == role_id, Role.company_id == company_id,
    ).with_for_update())
    if role is None:
        return None
    if role.is_system:
        raise ValueError("System roles cannot be deleted")
    assigned = session.scalar(select(membership_roles.c.membership_id).where(
        membership_roles.c.role_id == role.id,
    ).limit(1))
    if assigned is not None:
        raise ValueError("Reassign all memberships before deleting this role")
    role_name = role.name
    _audit(session, "identity_role_deleted", source_ip, user_id=actor_user_id,
           company_id=company_id, subject=role_name, details={"role_id": role.id})
    session.delete(role)
    session.commit()
    return True


def revoke_token(session: Session, token: str, source_ip: str | None = None) -> None:
    auth_session = session.scalar(select(AuthSession).where(AuthSession.token_digest == token_digest(token)))
    if auth_session is not None and auth_session.revoked_at is None:
        auth_session.revoked_at = datetime.now(timezone.utc)
        _audit(session, "logout_succeeded", source_ip, user_id=auth_session.user_id)
        session.commit()
