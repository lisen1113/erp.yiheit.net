"""Bounded, sanitized checks for the test deployment."""

import os
import socket
from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.pool import NullPool

from yihe_api.auth.security import auth_config
from yihe_api.db.session import DatabaseSettings, make_engine
from yihe_api.files.storage import S3Storage, storage

router = APIRouter()


@router.get("/ready", include_in_schema=False)
def ready() -> JSONResponse:
    checks = {
        "postgres": "unavailable",
        "migration": "unavailable",
        "redis": "unavailable",
        "secrets": "unavailable",
        "storage": "disabled",
        "clamav": "disabled",
    }
    try:
        engine = make_engine(DatabaseSettings.from_environment(), poolclass=NullPool)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            checks["postgres"] = "healthy"
            config = Config(str(Path.cwd() / "alembic.ini"))
            expected = ScriptDirectory.from_config(config).get_current_head()
            current = MigrationContext.configure(connection).get_current_heads()
            if expected and current == (expected,):
                checks["migration"] = "healthy"
    except Exception:
        pass

    try:
        with socket.create_connection((os.environ["ERP_REDIS_HOST"], 6379), timeout=2) as sock:
            sock.sendall(b"*1\r\n$4\r\nPING\r\n")
            with sock.makefile("rb") as response:
                if response.readline(32) == b"+PONG\r\n":
                    checks["redis"] = "healthy"
    except (KeyError, OSError):
        pass

    try:
        auth_config()
        if len(os.environ["ERP_AUDIT_IDENTIFIER_KEY"].encode()) >= 32:
            checks["secrets"] = "healthy"
    except (KeyError, RuntimeError):
        pass

    if os.environ.get("ERP_STORAGE_PROVIDER"):
        checks["storage"] = "unavailable"
        try:
            provider = storage()
            if isinstance(provider, S3Storage):
                result = provider.internal.get_bucket_versioning(Bucket=provider.bucket)
                if result.get("Status") == "Enabled":
                    checks["storage"] = "healthy"
        except Exception:
            # Provider exceptions can contain internal response details.
            pass
    if host := os.environ.get("ERP_CLAMD_HOST"):
        checks["clamav"] = "unavailable"
        try:
            port = int(os.environ.get("ERP_CLAMD_PORT", "3310"))
            with socket.create_connection((host, port), timeout=2) as sock:
                sock.sendall(b"nPING\n")
                with sock.makefile("rb") as response:
                    if response.readline(32) == b"PONG\n":
                        checks["clamav"] = "healthy"
        except (OSError, ValueError):
            pass

    healthy = all(state != "unavailable" for state in checks.values())
    return JSONResponse(
        {"status": "ready" if healthy else "unavailable", "checks": checks},
        status_code=200 if healthy else 503,
        headers={"Cache-Control": "no-store"},
    )
