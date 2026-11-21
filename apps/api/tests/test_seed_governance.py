from pathlib import Path
from runpy import run_path
from shutil import copytree

import pytest
from yihe_api import cli
from yihe_api.db.session import DatabaseSettings
from yihe_api.dev_seed import check_database

ROOT = Path(__file__).resolve().parents[3] / "scripts" / "seed"
VALIDATE = run_path(str(ROOT / "validate_fixtures.py"))["validate"]


@pytest.mark.parametrize("env", ["production", "", "staging"])
@pytest.mark.parametrize("action", ["seed-dev", "seed-reset"])
def test_cli_rejects_environment_before_engine(monkeypatch, capsys, env, action):
    monkeypatch.setenv("ERP_ENV", env)

    def unexpected(*args, **kwargs):
        pytest.fail("Forbidden environment attempted to construct a DB engine")

    monkeypatch.setattr(cli, "make_engine", unexpected)
    assert cli.run_seed(action) == 1
    assert "development or test" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("host", "database"),
    [("prod.example.invalid", "yihe_erp_seed_ci"), ("localhost", "yihe_erp")],
)
def test_database_guard_rejects_remote_or_nonseed_database(host, database):
    settings = DatabaseSettings.model_validate(
        {
            "host": host,
            "database": database,
            "user": "test",
            "password": "test-only",
        }
    )
    with pytest.raises(ValueError):
        check_database(settings)


def test_catalog_and_all_scenarios_are_valid():
    assert VALIDATE(ROOT) == {
        "departments": 2,
        "customers": 10,
        "suppliers": 5,
        "skus": 30,
        "warehouses": 3,
        "bank_accounts": 2,
        "brand_assets": 1,
        "scenarios": 10,
    }


@pytest.mark.parametrize(
    ("before", "after", "message"),
    [
        ('"C01"', '"MISSING"', "unknown fixture reference"),
        ('"CNY"', '"USD"', "CNY"),
        ('"2026-01-15"', '"2026-01-16"', "2026-01-15"),
        ('"quantity": 20', '"sale_amount": 0.1', "floats"),
        ('"quantity": 20', '"sale_amount": 2', "Decimal string"),
    ],
)
def test_rejects_invalid_scenario(tmp_path, before, after, message):
    for name in ("fixtures", "scenarios"):
        copytree(ROOT / name, tmp_path / name)
    first = tmp_path / "scenarios" / "S001.json"
    first.write_text(first.read_text(encoding="utf-8").replace(before, after), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        VALIDATE(tmp_path)
