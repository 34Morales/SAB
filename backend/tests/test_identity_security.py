import base64
import hashlib
import hmac
import struct

from backend.app.modules.identity.security import hash_password, verify_password, verify_totp


def test_password_hash_is_salted_and_verifiable():
    encoded = hash_password("correct-horse-battery")
    assert "correct-horse-battery" not in encoded
    assert verify_password("correct-horse-battery", encoded)
    assert not verify_password("wrong-password", encoded)
    assert encoded != hash_password("correct-horse-battery")


def test_totp_rfc6238_sha1_vector_and_replay_guard():
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert verify_totp(secret, "287082", now=59) == 1
    assert verify_totp(secret, "287082", now=59, last_step=1) is None


def test_totp_current_step_generation_matches_verifier():
    secret = base64.b32encode(b"a sufficiently long test key").decode().rstrip("=")
    step = 1_800_000_000 // 30
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = f"{(struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7fffffff) % 1_000_000:06d}"
    assert verify_totp(secret, code, now=1_800_000_000) == step
