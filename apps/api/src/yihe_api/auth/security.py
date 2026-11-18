"""Password, signed access token, and encrypted TOTP primitives."""

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from secrets import token_urlsafe
from uuid import uuid4

import jwt
import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from cryptography.fernet import Fernet

from yihe_api.auth.csrf import BrowserOrigin

ACCESS_SECONDS = 900
SESSION_SECONDS = 7 * 24 * 60 * 60
CHALLENGE_SECONDS = 300
LOCK_WINDOW = timedelta(minutes=15)
PASSWORD_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
ISSUER = "yihe-erp"


@dataclass(frozen=True, repr=False)
class AuthConfig:
    origin: BrowserOrigin
    signing_key: str
    cipher: Fernet


@lru_cache
def auth_config() -> AuthConfig:
    try:
        key = os.environ["ERP_AUTH_JWT_KEY"]
        encryption_key = os.environ["ERP_AUTH_MFA_KEY"]
        if len(key.encode()) < 32 or key == encryption_key:
            raise ValueError
        return AuthConfig(
            BrowserOrigin.parse(os.environ["ERP_AUTH_ORIGIN"]),
            key,
            Fernet(encryption_key.encode()),
        )
    except (KeyError, ValueError):
        raise RuntimeError("Missing or invalid authentication configuration") from None


@lru_cache
def dummy_hash() -> str:
    return PASSWORD_HASHER.hash(token_urlsafe(32))


def check_password(encoded: str, password: str) -> bool:
    try:
        return PASSWORD_HASHER.verify(encoded, password)
    except VerificationError:
        return False


def valid_new_password(password: str, username: str) -> bool:
    return (
        12 <= len(password) <= 128
        and bool(password.strip())
        and (password.strip().casefold() != username.strip().casefold())
    )


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def access_token(
    config: AuthConfig, *, user: str, company: str, session: str, now: datetime
) -> str:
    return jwt.encode(
        {
            "iss": ISSUER,
            "sub": user,
            "user_id": user,
            "company_id": company,
            "session_id": session,
            "iat": now,
            "exp": now + timedelta(seconds=ACCESS_SECONDS),
            "jti": uuid4().hex,
        },
        config.signing_key,
        algorithm="HS256",
    )


def verify_totp(
    config: AuthConfig, encrypted: str, code: str, now: datetime, previous_step: int | None
) -> int | None:
    from hmac import compare_digest

    totp = pyotp.TOTP(config.cipher.decrypt(encrypted.encode()).decode())
    step = int(now.timestamp()) // 30
    for candidate in (step, step - 1, step + 1):
        if (previous_step is None or candidate > previous_step) and compare_digest(
            totp.generate_otp(candidate), code
        ):
            return candidate
    return None
