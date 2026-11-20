"""Stable fingerprints over a business-only request projection."""

import hashlib
import json
import re
from decimal import Decimal, DecimalException
from uuid import UUID

KEY = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
MAX_PROJECTION_BYTES = 65536
SENSITIVE = frozenset(
    {
        "authorization",
        "cookie",
        "setcookie",
        "password",
        "passwordhash",
        "oldpassword",
        "newpassword",
        "accesstoken",
        "refreshtoken",
        "refreshtokenhash",
        "token",
        "csrftoken",
        "mfasecret",
        "mfacode",
        "verificationcode",
        "otp",
        "totp",
        "bankaccountnumber",
        "databaseurl",
        "encryptionkey",
    }
)


def normalize_key(raw: str) -> str:
    if len(raw) != 36 or not KEY.fullmatch(raw):
        raise ValueError("Idempotency-Key must be a canonical random UUIDv4")
    key = UUID(raw)
    if key.version != 4:
        raise ValueError("Idempotency-Key must be a canonical random UUIDv4")
    return str(key)


def json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    values = dict(pairs)
    if len(values) != len(pairs):
        raise ValueError("Duplicate JSON fields")
    return values


def reject_constant(_value: str) -> object:
    raise ValueError("Non-finite JSON number")


def parse_projection(raw: bytes) -> object:
    """Parse caller-selected JSON without silently converting business numbers to float."""
    if len(raw) > MAX_PROJECTION_BYTES:
        raise ValueError("Idempotency projection is too large")
    try:
        return json.loads(
            raw.decode("utf-8"),
            parse_float=Decimal,
            parse_int=Decimal,
            parse_constant=reject_constant,
            object_pairs_hook=json_object,
        )
    except (DecimalException, RecursionError):
        raise ValueError("Invalid numeric or nested idempotency projection") from None


def number(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("Non-finite idempotency number")
    if value.is_zero():
        return "0"
    sign, digits, exponent = value.as_tuple()
    end = len(digits)
    while end > 1 and digits[end - 1] == 0:
        end -= 1
    if not isinstance(exponent, int):
        raise ValueError("Non-finite idempotency number")
    coefficient = "".join(map(str, digits[:end]))
    return f"{'-' if sign else ''}{coefficient}e{exponent + len(digits) - end}"


def canonical(value: object, *, depth: int = 0) -> str:
    if depth > 32:
        raise ValueError("Idempotency projection is nested too deeply")
    if value is None or isinstance(value, (bool, str)):
        return json.dumps(value, ensure_ascii=True)
    if type(value) is int:
        return number(Decimal(value))
    if isinstance(value, Decimal):
        return number(value)
    if isinstance(value, list):
        items = (canonical(item, depth=depth + 1) for item in value)
        return "[" + ",".join(items) + "]"
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("Idempotency projection requires string keys")
        pairs = []
        for key, item in sorted(value.items()):
            name = re.sub(r"[^a-z0-9]", "", key.casefold())
            if name in SENSITIVE:
                raise ValueError("Sensitive fields are prohibited in idempotency projections")
            pairs.append(json.dumps(key) + ":" + canonical(item, depth=depth + 1))
        return "{" + ",".join(pairs) + "}"
    raise ValueError("Unsupported idempotency value; convert business numbers to Decimal")


def fingerprint(method: str, normalized_path: str, action: str, projection: object) -> str:
    # Paths and actions come from validated router bindings. Never pass raw URLs or headers.
    if "?" in normalized_path or "#" in normalized_path or not normalized_path.startswith("/"):
        raise ValueError("Idempotency fingerprint requires an absolute normalized path")
    payload = canonical([method.upper(), normalized_path, action, projection]).encode("ascii")
    if len(payload) > MAX_PROJECTION_BYTES:
        raise ValueError("Idempotency projection is too large")
    return hashlib.sha256(payload).hexdigest()
