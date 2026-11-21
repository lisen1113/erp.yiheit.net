"""Validate fixture references and numeric representation without writing to a database."""

import json
import re
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATE = "2026-01-15"
DECIMAL = re.compile(r"-?(?:0|[1-9][0-9]*)\.[0-9]{2,6}")
GROUPS = {
    "departments": 2,
    "customers": 10,
    "suppliers": 5,
    "skus": 30,
    "warehouses": 3,
    "bank_accounts": 2,
    "brand_assets": 1,
}


def reject_number(_raw: str) -> None:
    raise ValueError("Fixture numeric values must not use JSON floats or non-finite literals")


def load(path: Path) -> dict[str, object]:
    obj = json.loads(
        path.read_text(encoding="utf-8"), parse_float=reject_number, parse_constant=reject_number
    )
    if not isinstance(obj, dict):
        raise ValueError("Fixture root must be an object")
    return obj


def walk(values: object) -> Iterator[tuple[str, object]]:
    if isinstance(values, dict):
        for key, value in values.items():
            yield key, value
            yield from walk(value)
    elif isinstance(values, list):
        for value in values:
            yield from walk(value)


def validate_document(obj: dict[str, object]) -> None:
    if (
        obj.get("synthetic") is not True
        or obj.get("label") != "SYNTHETIC TEST DATA"
        or obj.get("business_date") != DATE
        or obj.get("currency_code") != "CNY"
    ):
        raise ValueError("Fixture must be declared synthetic, CNY and dated 2026-01-15")
    for key, value in walk(obj):
        if key == "currency_code" and value != "CNY":
            raise ValueError("Fixture is CNY-only")
        if key.endswith(("_amount", "_balance")) or key == "tax_rate":
            if not isinstance(value, str) or not DECIMAL.fullmatch(value):
                raise ValueError(f"Fixture {key} requires a Decimal string")
            Decimal(value)
        if key.endswith("email") and (
            not isinstance(value, str) or not value.endswith("@example.invalid")
        ):
            raise ValueError("Fixture email must use example.invalid")
        if key == "account_no" and (
            not isinstance(value, str) or not re.fullmatch(r"TEST-ACCOUNT-[0-9]{4}", value)
        ):
            raise ValueError("Fixture bank accounts must be visibly synthetic")


def validate(root: Path = ROOT) -> dict[str, int]:
    master = load(root / "fixtures/master_data.json")
    validate_document(master)
    catalog: dict[str, set[str]] = {}
    for group, expected in GROUPS.items():
        rows = master.get(group)
        if not isinstance(rows, list) or len(rows) != expected:
            raise ValueError(f"{group}: wrong fixture count")
        ids = set()
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                raise ValueError(f"{group}: invalid record")
            if group in {"customers", "suppliers"} and not re.fullmatch(
                r"上海示例(?:客户|供应商)[0-9]{2}", row.get("name", "")
            ):
                raise ValueError("Party name must be visibly synthetic")
            ids.add(row["id"])
        if len(ids) != expected:
            raise ValueError(f"{group}: duplicate fixture ID")
        catalog[group] = ids
    paths = sorted((root / "scenarios").glob("*.json"))
    if [path.stem for path in paths] != [f"S{number:03}" for number in range(1, 11)]:
        raise ValueError("Expected numbered S001–S010 scenarios")
    for path in paths:
        scenario = load(path)
        validate_document(scenario)
        references = scenario.get("references")
        if scenario.get("id") != path.stem or not isinstance(references, dict) or not references:
            raise ValueError("Scenario ID or references are missing")
        if not scenario.get("steps") or not isinstance(scenario.get("inputs"), dict):
            raise ValueError("Scenario steps and inputs are required")
        for group, values in references.items():
            if (
                group not in catalog
                or not isinstance(values, list)
                or not values
                or not all(isinstance(value, str) and value in catalog[group] for value in values)
            ):
                raise ValueError(f"{path.stem}: unknown fixture reference in {group}")
    return GROUPS | {"scenarios": len(paths)}


if __name__ == "__main__":
    print(json.dumps(validate(), sort_keys=True))
