from decimal import Decimal, localcontext
from uuid import uuid4

import pytest
from yihe_api.idempotency.fingerprint import fingerprint, normalize_key, parse_projection


def digest(value: object) -> str:
    return fingerprint("POST", "/api/v1/test", "test.submit", value)


def test_valid_key_normalizes_case() -> None:
    key = str(uuid4())
    assert normalize_key(key.upper()) == key


@pytest.mark.parametrize(
    "value", ["", "a" * 129, "not-a-key", "11111111-1111-1111-8111-111111111111"]
)
def test_invalid_keys_are_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_key(value)


def test_json_fingerprint_ignores_order_whitespace_and_numeric_spelling() -> None:
    left = parse_projection(b'{"total": 0.30, "lines": [1, 2]}')
    right = parse_projection(b'{ "lines":[1.0,2e0], "total":3e-1}')
    assert digest(left) == digest(right)


def test_fingerprint_preserves_decimal_precision_without_context_rounding() -> None:
    with localcontext() as context:
        context.prec = 3
        assert digest(Decimal("123456789.1234567800")) == digest(Decimal("123456789.12345678"))
        assert digest(Decimal("123456789.12345678")) != digest(Decimal("123456789.12345679"))


def test_fingerprint_preserves_null_missing_array_order_and_value_type() -> None:
    assert digest({}) != digest({"a": None})
    assert digest({"a": [1, 2]}) != digest({"a": [2, 1]})
    assert digest({"a": 1}) != digest({"a": "1"})
    assert digest({"a": True}) != digest({"a": 1})


@pytest.mark.parametrize("body", [b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}'])
def test_noncanonical_json_is_rejected(body: bytes) -> None:
    with pytest.raises(ValueError):
        parse_projection(body)


def test_sensitive_values_and_floats_are_rejected() -> None:
    with pytest.raises(ValueError, match="Sensitive"):
        digest({"nested": [{"refresh_token": "synthetic"}]})
    with pytest.raises(ValueError, match="Decimal"):
        digest({"total": 0.3})
