from uuid import uuid4

import pytest
from yihe_api.audit.payload import changed_fields, permission_diff, sanitize


@pytest.mark.parametrize(
    "field",
    [
        "password",
        "password_hash",
        "token",
        "access_token",
        "refresh_token_hash",
        "cookie",
        "mfa_secret",
        "otp",
        "encryption_key",
        "database_url",
        "bank_account_number",
        "national_id",
        "contract_body",
        "file_content",
        "raw_import",
    ],
)
def test_unknown_and_sensitive_fields_are_omitted(field: str) -> None:
    assert sanitize({field: "synthetic value", "password_changed": True}) == {
        "password_changed": True,
    }


def test_last_four_and_identifier_validate_without_free_text() -> None:
    account_id = uuid4()
    assert sanitize({"account_id": account_id, "bank_last_four": "1234"}) == {
        "account_id": str(account_id),
        "bank_last_four": "1234",
    }
    with pytest.raises(ValueError, match="bank_last_four"):
        sanitize({"bank_last_four": "1234567890123456"})


def test_diff_only_keeps_changed_registered_fields() -> None:
    actor = uuid4()
    before = {"status": "active", "employee_id": actor, "password_hash": "discard one"}
    after = {"status": "disabled", "employee_id": actor, "password_hash": "discard two"}
    assert changed_fields(before, after) == ({"status": "active"}, {"status": "disabled"})


def test_permission_delta_is_sorted_and_deduplicated() -> None:
    before = ["system.user.view", "system.role.manage"]
    after = ["system.audit.view", "company.profile.view", "system.user.view", "system.audit.view"]
    assert permission_diff(before, after) == {
        "added": ["company.profile.view", "system.audit.view"],
        "removed": ["system.role.manage"],
    }


def test_large_permission_delta_is_summarized() -> None:
    result = permission_diff([], [f"report.item_{n}.view" for n in range(201)])
    assert result == {"added_count": 201, "removed_count": 0}
