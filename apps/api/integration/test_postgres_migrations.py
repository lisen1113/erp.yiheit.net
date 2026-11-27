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
CUSTOMER_TABLES = sorted([*PARTNER_TABLES, "customer_profile"])
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


SUPPLIER_TABLES = sorted([*CUSTOMER_TABLES, "supplier_profile"])


CATEGORY_TABLES = sorted([*SUPPLIER_TABLES, "product_category"])


MASTER_TABLES = sorted(
    [
        *CATEGORY_TABLES,
        "legacy_product_map",
        "product_brand",
        "product_unit",
        "product_spu",
        "product_sku",
        "product_tracking_mode_change",
    ]
)


BATCH_TABLES = sorted([*MASTER_TABLES, "product_import_batch"])


@pytest.fixture(scope="module")
def migrated_engine():
    engine = make_engine(DatabaseSettings.from_environment())
    try:
        with engine.connect() as connection:
            assert 160000 <= int(connection.scalar(text("SHOW server_version_num"))) < 170000
            assert inspect(connection).get_table_names() == []
        assert len(alembic("heads").splitlines()) == 1
        alembic("upgrade", "head")
        assert sorted(inspect(engine).get_table_names()) == BATCH_TABLES
        alembic("downgrade", "-1")
        assert sorted(inspect(engine).get_table_names()) == MASTER_TABLES
        alembic("downgrade", "-1")
        assert sorted(inspect(engine).get_table_names()) == CATEGORY_TABLES
        alembic("upgrade", "head")
        alembic("downgrade", "-2")
        alembic("downgrade", "-1")
        assert sorted(inspect(engine).get_table_names()) == SUPPLIER_TABLES
        alembic("upgrade", "0012_product_category")
        alembic("downgrade", "-2")
        assert sorted(inspect(engine).get_table_names()) == CUSTOMER_TABLES
        alembic("upgrade", "0010_partner_bank_account_width")
        actor = uuid4()
        with engine.begin() as connection:
            company = connection.scalar(
                text("""
                    INSERT INTO company (code, name, created_by, updated_by)
                    VALUES ('TAX-ROUNDTRIP', 'Synthetic migration company', :actor, :actor)
                    RETURNING id
                """),
                {"actor": actor},
            )
            connection.execute(
                text("""
                    INSERT INTO business_partner
                        (company_id, code, name, partner_type, tax_no, created_by, updated_by)
                    VALUES (:company, 'TAX-01', 'Synthetic partner', 'BOTH',
                            'ABC-123', :actor, :actor)
                """),
                {"company": company, "actor": actor},
            )
        alembic("upgrade", "0012_product_category")
        with engine.begin() as connection:
            connection.execute(
                text("""
                    INSERT INTO product_category
                        (company_id, code, name, normalized_name, level, path,
                         created_by, updated_by)
                    VALUES (:company, 'ROUNDTRIP', 'Synthetic category', 'synthetic category',
                            0, '/synthetic', :actor, :actor)
                """),
                {"company": company, "actor": actor},
            )
        with pytest.raises(subprocess.CalledProcessError):
            alembic("downgrade", "-1")
        with engine.begin() as connection:
            assert connection.scalar(text("SELECT count(*) FROM product_category")) == 1
            connection.execute(text("DELETE FROM product_category WHERE code = 'ROUNDTRIP'"))
        alembic("downgrade", "0010_partner_bank_account_width")
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE business_partner SET invoice_bank_account = repeat('X', 255)")
            )
        with pytest.raises(subprocess.CalledProcessError):
            alembic("downgrade", "-1")
        with engine.begin() as connection:
            assert (
                connection.scalar(
                    text("SELECT char_length(invoice_bank_account) FROM business_partner")
                )
                == 255
            )
            connection.execute(
                text("UPDATE business_partner SET invoice_bank_account = 'SYNTHETIC1234'")
            )
        alembic("downgrade", "-1")
        assert sorted(inspect(engine).get_table_names()) == CUSTOMER_TABLES
        bank_column = next(
            c
            for c in inspect(engine).get_columns("business_partner")
            if c["name"] == "invoice_bank_account"
        )
        assert str(bank_column["type"]) == "VARCHAR(128)"
        alembic("upgrade", "head")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT invoice_bank_account FROM business_partner")) == (
                "SYNTHETIC1234"
            )
        alembic("downgrade", "0008_partner_tax_no")
        assert sorted(inspect(engine).get_table_names()) == PARTNER_TABLES
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0008_partner_tax_no"
            )
        alembic("downgrade", "-1")
        assert "invoice_tax_no" in {
            c["name"] for c in inspect(engine).get_columns("business_partner")
        }
        with engine.begin() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0007_business_partner"
            )
            assert connection.scalar(text("SELECT invoice_tax_no FROM business_partner")) == (
                "ABC-123"
            )
            connection.execute(
                text("UPDATE business_partner SET invoice_tax_no = :raw"),
                {"raw": "　ａｂ ｃ－１２３　"},
            )
        alembic("upgrade", "head")
        assert sorted(inspect(engine).get_table_names()) == BATCH_TABLES
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text("UPDATE business_partner SET tax_no = ' abc-123 '"))
        with engine.begin() as connection:
            assert connection.scalar(text("SELECT tax_no FROM business_partner")) == "ABC-123"
            connection.execute(
                text("DELETE FROM business_partner WHERE company_id = :id"), {"id": company}
            )
            connection.execute(text("DELETE FROM company WHERE id = :id"), {"id": company})
        alembic("check")
        yield engine
    finally:
        engine.dispose()


def test_upgrade_downgrade_and_reupgrade(migrated_engine) -> None:
    with migrated_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0014_product_import_batch"
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
