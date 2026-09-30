from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

import jwt

from goalcoach.infrastructure.config import Settings

_ALGORITHM = "HS256"
_PBKDF2_ITERATIONS = 200_000


def hash_password(password: str, *, salt_hex: str | None = None) -> tuple[str, str]:
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return digest.hex(), salt.hex()


def verify_password(password: str, *, password_hash: str, salt_hex: str) -> bool:
    computed_hash, _ = hash_password(password, salt_hex=salt_hex)
    return hmac.compare_digest(computed_hash, password_hash)


def create_access_token(user_id: str, settings: Settings | None = None) -> str:
    resolved = settings or Settings()
    issued_at = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "iat": int(issued_at.timestamp()),
        "exp": int(
            (issued_at + timedelta(minutes=resolved.auth_token_expiry_minutes)).timestamp()
        ),
    }
    return jwt.encode(payload, resolved.auth_secret_key, algorithm=_ALGORITHM)


def decode_access_token(token: str, settings: Settings | None = None) -> str | None:
    resolved = settings or Settings()
    try:
        payload = jwt.decode(token, resolved.auth_secret_key, algorithms=[_ALGORITHM])
    except jwt.PyJWTError:
        return None
    subject = payload.get("sub")
    return str(subject) if subject else None
