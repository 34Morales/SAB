"""Password hashing and opaque bearer-session token helpers."""

import hashlib
import hmac
import base64
import struct
import secrets
import time
from urllib.parse import quote

from backend.app.core.config import get_settings

_ITERATIONS = 600_000


def hash_password(password: str) -> str:
    if len(password) < 12 or len(password) > 1024:
        raise ValueError("Password must contain between 12 and 1024 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = encoded.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def issue_token() -> str:
    return secrets.token_urlsafe(48)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def subject_digest(value: str) -> str:
    pepper = get_settings().auth_throttle_pepper.encode()
    return hmac.new(pepper, value.encode(), hashlib.sha256).hexdigest()


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def verify_totp(secret: str, code: str, *, now: int | None = None, last_step: int | None = None) -> int | None:
    if not code.isdigit() or len(code) != 6:
        return None
    timestamp = int(time.time()) if now is None else now
    current_step = timestamp // 30
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    for step in (current_step - 1, current_step, current_step + 1):
        if step < 0 or (last_step is not None and step <= last_step):
            continue
        digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
        offset = digest[-1] & 0x0F
        value = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
        if hmac.compare_digest(f"{value:06d}", code):
            return step
    return None


def encrypt_secret(secret: str) -> str:
    from cryptography.fernet import Fernet

    key = get_settings().mfa_encryption_key
    if not key:
        raise RuntimeError("MFA_ENCRYPTION_KEY is not configured")
    try:
        return Fernet(key.encode()).encrypt(secret.encode()).decode()
    except (ValueError, TypeError) as exc:
        raise RuntimeError("MFA_ENCRYPTION_KEY is invalid") from exc


def decrypt_secret(encrypted: str) -> str:
    from cryptography.fernet import Fernet, InvalidToken

    key = get_settings().mfa_encryption_key
    if not key:
        raise RuntimeError("MFA_ENCRYPTION_KEY is not configured")
    try:
        return Fernet(key.encode()).decrypt(encrypted.encode()).decode()
    except (ValueError, TypeError, InvalidToken) as exc:
        raise RuntimeError("Could not decrypt MFA data; verify MFA_ENCRYPTION_KEY") from exc


def totp_uri(secret: str, email: str) -> str:
    label = quote(f"ERP Empresarial:{email}")
    return f"otpauth://totp/{label}?secret={secret}&issuer=ERP%20Empresarial&algorithm=SHA1&digits=6&period=30"


def new_recovery_codes(count: int = 10) -> list[str]:
    return [secrets.token_hex(8).upper() for _ in range(count)]
