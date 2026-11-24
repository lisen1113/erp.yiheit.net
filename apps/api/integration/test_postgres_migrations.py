"""Run against an empty disposable PostgreSQL 16 database."""

import subprocess
import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import MetaData, cast, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.schema import CreateTable
from yihe_api.db.models import AMOUNT, QUANTITY, TAX_RATE, UNIT_COST, Company, CompanyScopeMixin
from yihe_api.db.session import DatabaseSettings, make_engine, make_session_factory

AUTH_TABLES = [
    "alembic_version",
    "app_user",
    "auth_login_challenge",
    "auth_refresh_grant",
    "auth_session",
    "company",
    "employee",
]
RBAC_TABLES = sorted([*AUTH_TABLES, "permission", "role", "role_permission", "user_role"])
AUDIT_TABLES = sorted([*RBAC_TABLES, "audit_log", "security_event"])
IDEM_TABLES = sorted([*AUDIT_TABLES, "idempotency_record"])
FILE_TABLES = sorted([*IDEM_TABLES, "brand_asset", "file_asset"])
PARTNER_TABLES = sorted([*FILE_TABLES, "business_partner"])
CONFIG = Path(__file__).resolve().parents[1] / "alembic.ini"


def alembic(*args: str) -> str:
    print(f"$ alembic -c apps/api/alembic.ini {' '.join(args)}", flush=True)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(CONFIG), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    print(result.stdout + result.stderr, end="")
    result.check_returncode()
    return result.stdout


@pytest.fixture(scope="module")
def migrated_engine():
    engine = make_engine(DatabaseSettings.from_environment())
    try:
        with engine.connect() as connection:
            assert 160000 <= int(connection.scalar(text("SHOW server_version_num"))) < 170000
            assert inspect(connection).get_table_names() == []
        assert len(alembic("heads").splitlines()) == 1
        alembic("upgrade", "head")
        assert sorted(inspect(engine).get_table_names()) == PARTNER_TABLES
        alembic("downgrade", "-1")
        assert sorted(inspect(engine).get_table_names()) == FILE_TABLES
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0006_file_brand_assets"
            )
        alembic("upgrade", "head")
        assert sorted(inspect(engine).get_table_names()) == PARTNER_TABLES
        alembic("check")
        yield engine
    finally:
        engine.dispose()


def test_upgrade_downgrade_and_reupgrade(migrated_engine) -> None:
    with migrated_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0007_business_partner"
        )


def test_company_uuid_audit_utc_and_optimistic_version(migrated_engine) -> None:
    actor = uuid4()
    factory = make_session_factory(migrated_engine)
    with factory.begin() as session:
        assert session.scalar(text("SHOW timezone")) == "UTC"
        record = Company(code="TEST", name="Synthetic company", created_by=actor, updated_by=actor)
        session.add(record)
        session.flush()
        assert isinstance(record.id, UUID)
        assert record.id.version == 4
        assert record.created_at.utcoffset() == timedelta(0)
        assert record.updated_at.utcoffset() == timedelta(0)
        assert record.version_no == 1
        assert record.is_deleted is False
        record.name = "Synthetic company updated"
        session.flush()
        assert record.version_no == 2


def test_postgresql_numeric_precision(migrated_engine) -> None:
    assert str((Decimal("0.1") + Decimal("0.2")).quantize(Decimal("0.00"))) == "0.30"
    with migrated_engine.connect() as connection:
        lhs = cast(Decimal("0.1"), AMOUNT)
        rhs = cast(Decimal("0.2"), AMOUNT)
        result = connection.scalar(select(cast(lhs + rhs, AMOUNT)))
        assert isinstance(result, Decimal)
        assert result == Decimal("0.30")
        assert str(result) == "0.30"

        for kind, value, scale in (
            (QUANTITY, "1.2345", 4),
            (UNIT_COST, "1.234567", 6),
            (TAX_RATE, "0.1300", 4),
        ):
            actual = connection.scalar(select(cast(Decimal(value), kind)))
            assert isinstance(actual, Decimal)
            assert actual.as_tuple().exponent == -scale
            assert str(actual) == value


def test_company_scope_rejects_missing_and_unknown_company(migrated_engine) -> None:
    class ProbeBase(DeclarativeBase):
        metadata = MetaData()

    Company.__table__.to_metadata(ProbeBase.metadata)

    class ScopeProbe(CompanyScopeMixin, ProbeBase):
        __tablename__ = "t003_scope_probe"
        id: Mapped[int] = mapped_column(primary_key=True)

    with migrated_engine.connect() as connection, connection.begin() as transaction:
        connection.execute(CreateTable(ScopeProbe.__table__))
        for value, sqlstate in ((None, "23502"), (uuid4(), "23503")):
            with (
                pytest.raises(IntegrityError) as error,
                connection.begin_nested(),
            ):
                connection.execute(ScopeProbe.__table__.insert().values(company_id=value))
            assert error.value.orig.sqlstate == sqlstate
        transaction.rollback()
