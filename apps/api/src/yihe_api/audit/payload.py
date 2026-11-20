"""Allowlist audit payloads: only explicitly classified fields may be retained."""

import re
from collections.abc import Iterable, Mapping
from enum import StrEnum
from uuid import UUID

type AuditValue = bool | int | str | list[str]

PERMISSION_CODE = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){1,2}")
LAST_FOUR = re.compile(r"[0-9]{4}")
MAX_SET_ITEMS = 200


class Field(StrEnum):
    ACTIVE = "is_active"
    STATUS = "status"
    DATA_SCOPE = "data_scope"
    REQUIRES_MFA = "requires_mfa"
    PASSWORD_CHANGED = "password_changed"
    MFA_EVENT = "mfa_event"
    BANK_LAST_FOUR = "bank_last_four"
    BANK_CHANGE = "bank_change"
    ROLE_ID = "role_id"
    ACCOUNT_ID = "account_id"
    EMPLOYEE_ID = "employee_id"
    RELATED_ID = "related_id"
    ITEM_COUNT = "item_count"
    ADDED_COUNT = "added_count"
    REMOVED_COUNT = "removed_count"
    KEY_HASH = "idempotency_key_hash"
    REQUEST_HASH = "idempotency_request_hash"
    IDEMPOTENCY_STATUS = "idempotency_status"
    IDEMPOTENCY_ACTION = "idempotency_action"
    ADDED = "added"
    REMOVED = "removed"


BOOL_FIELDS = frozenset({Field.ACTIVE, Field.REQUIRES_MFA, Field.PASSWORD_CHANGED})
UUID_FIELDS = frozenset({Field.ROLE_ID, Field.ACCOUNT_ID, Field.EMPLOYEE_ID, Field.RELATED_ID})
SET_FIELDS = frozenset({Field.ADDED, Field.REMOVED})
ENUM_FIELDS = {
    Field.STATUS: frozenset({"active", "disabled"}),
    Field.DATA_SCOPE: frozenset({"SELF", "DEPARTMENT", "COMPANY"}),
    Field.MFA_EVENT: frozenset({"setup", "enabled", "disabled", "verified", "failed"}),
    Field.BANK_CHANGE: frozenset({"created", "updated", "disabled"}),
}


def permission_codes(values: Iterable[str]) -> list[str]:
    unique = sorted(set(values))
    if len(unique) > MAX_SET_ITEMS:
        raise ValueError("Audit permission set exceeds limit")
    if any(len(code) > 100 or not PERMISSION_CODE.fullmatch(code) for code in unique):
        raise ValueError("Invalid audit permission code")
    return unique


def validate(field: Field, value: object) -> AuditValue:
    if field in {Field.KEY_HASH, Field.REQUEST_HASH} and isinstance(value, str):
        if re.fullmatch(r"[0-9a-f]{64}", value):
            return value
    if field == Field.IDEMPOTENCY_ACTION and isinstance(value, str):
        if len(value) <= 64 and re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){1,3}", value):
            return value
    if field == Field.IDEMPOTENCY_STATUS and isinstance(value, str):
        if value in {"IN_PROGRESS", "COMPLETED", "FAILED_RETRYABLE", "FAILED_FINAL", "EXPIRED"}:
            return value
    if field in BOOL_FIELDS and isinstance(value, bool):
        return value
    if field in UUID_FIELDS and isinstance(value, UUID):
        return str(value)
    if field in ENUM_FIELDS and isinstance(value, str) and value in ENUM_FIELDS[field]:
        return value
    if field == Field.BANK_LAST_FOUR and isinstance(value, str) and LAST_FOUR.fullmatch(value):
        return value
    if (
        field in {Field.ITEM_COUNT, Field.ADDED_COUNT, Field.REMOVED_COUNT}
        and type(value) is int
        and 0 <= value <= 2**31 - 1
    ):
        return value
    if field in SET_FIELDS and isinstance(value, (list, tuple)):
        if all(isinstance(item, str) for item in value):
            return permission_codes(value)
    raise ValueError(f"Invalid audit field: {field.value}")


def sanitize(values: Mapping[str, object]) -> dict[str, AuditValue]:
    """Discard unregistered keys, including credentials and unclassified free text."""
    result: dict[str, AuditValue] = {}
    for field in sorted(Field):
        value = values.get(field)
        if value is not None:
            result[field.value] = validate(field, value)
    return result


def changed_fields(
    before: Mapping[str, object],
    after: Mapping[str, object],
) -> tuple[dict[str, AuditValue], dict[str, AuditValue]]:
    old = sanitize(before)
    new = sanitize(after)
    changed = sorted(key for key in old.keys() | new.keys() if old.get(key) != new.get(key))
    return (
        {key: old[key] for key in changed if key in old},
        {key: new[key] for key in changed if key in new},
    )


def permission_diff(before: Iterable[str], after: Iterable[str]) -> dict[str, AuditValue]:
    old, new = set(before), set(after)
    added, removed = new - old, old - new
    if len(added) > MAX_SET_ITEMS or len(removed) > MAX_SET_ITEMS:
        return {
            "added_count": len(added),
            "removed_count": len(removed),
        }
    return {"added": permission_codes(added), "removed": permission_codes(removed)}
