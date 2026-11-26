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
    BANK_ACCOUNT_CHANGED = "bank_account_changed"
    PREVIOUS_CREDIT = "previous_credit"
    CURRENT_CREDIT = "current_credit"
    ROLE_ID = "role_id"
    ACCOUNT_ID = "account_id"
    EMPLOYEE_ID = "employee_id"
    RELATED_ID = "related_id"
    ITEM_COUNT = "item_count"
    FILE_TYPE = "file_asset_type"
    FILE_MIME = "file_content_type"
    FILE_SIZE = "file_size_bytes"
    FILE_HASH = "file_sha256"
    FILE_STATUS = "file_status"
    ADDED_COUNT = "added_count"
    REMOVED_COUNT = "removed_count"
    KEY_HASH = "idempotency_key_hash"
    REQUEST_HASH = "idempotency_request_hash"
    IDEMPOTENCY_STATUS = "idempotency_status"
    IDEMPOTENCY_ACTION = "idempotency_action"
    ADDED = "added"
    REMOVED = "removed"
    SCHEMA_ADDED = "schema_added"
    SCHEMA_REMOVED = "schema_removed"
    SCHEMA_TYPE_CHANGED = "schema_type_changed"
    SCHEMA_ENUM_CHANGED = "schema_enum_changed"
    SCHEMA_REQUIRED_BEFORE = "schema_required_before"
    SCHEMA_REQUIRED_AFTER = "schema_required_after"
    SCHEMA_VERSION_BEFORE = "schema_version_before"
    SCHEMA_VERSION_AFTER = "schema_version_after"
    SCHEMA_COMPATIBILITY = "schema_compatibility"
    TRACKING_MODE_BEFORE = "tracking_mode_before"
    TRACKING_MODE_AFTER = "tracking_mode_after"
    MAKER_ID = "maker_id"
    APPROVER_ID = "approver_id"
    UPDATED_COUNT = "updated_count"
    ERROR_COUNT = "error_count"


BOOL_FIELDS = frozenset(
    {Field.ACTIVE, Field.REQUIRES_MFA, Field.PASSWORD_CHANGED, Field.BANK_ACCOUNT_CHANGED}
)
UUID_FIELDS = frozenset(
    {
        Field.ROLE_ID,
        Field.ACCOUNT_ID,
        Field.EMPLOYEE_ID,
        Field.RELATED_ID,
        Field.MAKER_ID,
        Field.APPROVER_ID,
    }
)
SET_FIELDS = frozenset({Field.ADDED, Field.REMOVED})
ENUM_FIELDS = {
    Field.TRACKING_MODE_BEFORE: frozenset({"quantity", "serial", "batch", "non_stock"}),
    Field.TRACKING_MODE_AFTER: frozenset({"quantity", "serial", "batch", "non_stock"}),
    Field.STATUS: frozenset({"active", "disabled"}),
    Field.DATA_SCOPE: frozenset({"SELF", "DEPARTMENT", "COMPANY"}),
    Field.MFA_EVENT: frozenset({"setup", "enabled", "disabled", "verified", "failed"}),
    Field.SCHEMA_COMPATIBILITY: frozenset(
        {"BACKWARD_COMPATIBLE", "REQUIRES_DATA_REVALIDATION", "BREAKING"}
    ),
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
    if field == Field.FILE_TYPE and value in ("BRAND", "ATTACHMENT", "IMPORT"):
        return str(value)
    if field == Field.FILE_MIME and isinstance(value, str) and len(value) <= 128:
        if re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", value):
            return value
    if field == Field.FILE_SIZE and type(value) is int and 0 < value <= 524288000:
        return value
    if field == Field.FILE_HASH and isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value):
        return value
    if (
        field == Field.FILE_STATUS
        and isinstance(value, str)
        and value
        in {
            "UPLOADING",
            "AVAILABLE",
            "QUARANTINED",
            "REJECTED",
            "EXPIRED",
            "INVALIDATED",
            "DELETED_FROM_STORAGE",
        }
    ):
        return value
    if field in {Field.PREVIOUS_CREDIT, Field.CURRENT_CREDIT} and isinstance(value, str):
        if re.fullmatch(r"[0-9]{1,16}(?:\.[0-9]{1,2})?", value):
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
        field
        in {
            Field.ITEM_COUNT,
            Field.ADDED_COUNT,
            Field.REMOVED_COUNT,
            Field.UPDATED_COUNT,
            Field.ERROR_COUNT,
            Field.SCHEMA_VERSION_BEFORE,
            Field.SCHEMA_VERSION_AFTER,
        }
        and type(value) is int
        and 0 <= value <= 2**31 - 1
    ):
        return value
    if (
        field
        in {
            Field.SCHEMA_ADDED,
            Field.SCHEMA_REMOVED,
            Field.SCHEMA_TYPE_CHANGED,
            Field.SCHEMA_ENUM_CHANGED,
            Field.SCHEMA_REQUIRED_BEFORE,
            Field.SCHEMA_REQUIRED_AFTER,
        }
        and isinstance(value, list)
        and len(value) <= 64
    ):
        if all(
            isinstance(key, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key) for key in value
        ):
            return sorted(set(value))
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
