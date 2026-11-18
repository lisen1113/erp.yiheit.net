"""Exercise authentication against the migrated PostgreSQL 16 database."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from threading import Barrier
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import jwt
import pyotp
import pytest
from argon2 import extract_parameters
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select
from yihe_api.auth import router as auth
from yihe_api.auth.models import Employee, RefreshGrant, User
from yihe_api.auth.security import PASSWORD_HASHER, auth_config, digest
from yihe_api.db.models import Company
from yihe_api.main import app

ORIGIN = "https://erp.example.test"
BASE = "/api/v1/auth"
HEADERS = {"Origin": ORIGIN}
COOKIE = auth.COOKIE


@pytest.fixture
def identity(monkeypatch):
    monkeypatch.setenv("ERP_AUTH_ORIGIN", ORIGIN)
    monkeypatch.setenv("ERP_AUTH_JWT_KEY", token_urlsafe(48))
    monkeypatch.setenv("ERP_AUTH_MFA_KEY", Fernet.generate_key().decode())
    auth_config.cache_clear()
    actor = uuid4()
    password = token_urlsafe(24)

    with auth.session_factory().begin() as db:
        company = Company(code=uuid4().hex[:20], name="Test", created_by=actor, updated_by=actor)
        db.add(company)
        db.flush()
        employee = Employee(
            company_id=company.id,
            employee_no="TEST",
            name="Test",
            created_by=actor,
            updated_by=actor,
        )
        db.add(employee)
        db.flush()
        user = User(
            company_id=company.id,
            employee_id=employee.id,
            username=uuid4().hex,
            password_hash=PASSWORD_HASHER.hash(password),
            created_by=actor,
            updated_by=actor,
        )
        db.add(user)
        db.flush()
        user_id = user.id
        username = user.username

    with TestClient(app, base_url=ORIGIN) as client:
        yield client, user_id, {"username": username, "password": password}
    auth_config.cache_clear()


def post(client, path, *, payload=None, bearer=None, refresh=None):
    headers = dict(HEADERS)
    if bearer is not None:
        headers["Authorization"] = f"Bearer {bearer}"
    if refresh is not None:
        headers["Cookie"] = f"{COOKIE}={refresh}"
    return client.post(BASE + path, json=payload, headers=headers)


def login(client, credentials):
    result = post(client, "/login", payload=credentials)
    assert result.status_code == 200
    return result


def cookie(result):
    value = result.cookies.get(COOKIE)
    assert value is not None
    return value


def test_argon2id_login_cookie_t004_trace_and_expired_access(identity):
    client, user_id, credentials = identity
    with auth.session_factory()() as db:
        account = db.get(User, user_id)
        assert account is not None
        params = extract_parameters(account.password_hash)
        assert params.type.name == "ID"
        assert (params.time_cost, params.memory_cost, params.parallelism) == (3, 65536, 4)
    result = login(client, credentials)
    access = result.json()["access_token"]
    headers = result.headers["Set-Cookie"].lower()
    assert all(
        part in headers
        for part in ("httponly", "secure", "samesite=lax", "path=/api/v1/auth", "max-age=604800")
    )
    assert result.json()["expires_in"] == 900
    assert result.headers.get("X-Request-ID")
    assert (
        client.get(BASE + "/me", headers={"Authorization": f"Bearer {access}"}).status_code == 200
    )
    config = auth_config()
    claims = jwt.decode(access, config.signing_key, algorithms=["HS256"], issuer="yihe-erp")
    claims["exp"] = datetime.now(UTC) - timedelta(seconds=1)
    expired = jwt.encode(claims, config.signing_key, algorithm="HS256")
    assert (
        client.get(BASE + "/me", headers={"Authorization": f"Bearer {expired}"}).status_code == 401
    )


def test_unknown_username_wrong_password_lockout_and_reset(identity, monkeypatch):
    client, user_id, credentials = identity
    now = datetime.now(UTC)
    monkeypatch.setattr(auth, "now_utc", lambda: now)
    bad = {"username": credentials["username"], "password": token_urlsafe(24)}
    unknown = post(client, "/login", payload={**bad, "username": uuid4().hex})
    first = post(client, "/login", payload=bad)
    assert unknown.status_code == first.status_code == 401
    assert first.json()["code"] == unknown.json()["code"] == "AUTH_INVALID_CREDENTIALS"
    assert first.json()["message"] == unknown.json()["message"]
    assert first.json()["trace_id"] == first.headers["X-Request-ID"]
    login(client, credentials)
    with auth.session_factory()() as db:
        assert db.get(User, user_id).failed_login_count == 0
    for _ in range(5):
        assert post(client, "/login", payload=bad).status_code == 401
    with auth.session_factory()() as db:
        account = db.get(User, user_id)
        assert account.failed_login_count == 5
        assert account.locked_until == now + timedelta(minutes=15)
    now += timedelta(minutes=14, seconds=59)
    assert post(client, "/login", payload=credentials).status_code == 401
    now += timedelta(seconds=1)
    login(client, credentials)


def test_mfa_then_rotation_then_logout_and_secrets(identity, monkeypatch, capfd):
    client, user_id, credentials = identity
    now = datetime.now(UTC)
    monkeypatch.setattr(auth, "now_utc", lambda: now)
    with auth.session_factory().begin() as db:
        db.get(User, user_id).mfa_required = True
    first = login(client, credentials)
    assert first.json()["status"] == "mfa_setup_required"
    assert "access_token" not in first.json() and "Set-Cookie" not in first.headers
    challenge = first.json()["challenge"]
    setup = post(
        client, "/mfa/setup", payload={"challenge": challenge, "password": credentials["password"]}
    )
    assert setup.status_code == 200
    secret = parse_qs(urlsplit(setup.json()["otpauth_uri"]).query)["secret"][0]
    with auth.session_factory()() as db:
        encrypted = db.get(User, user_id).mfa_secret_encrypted
        assert encrypted != secret
        assert auth_config().cipher.decrypt(encrypted.encode()).decode() == secret
    enable = post(
        client, "/mfa/enable", payload={"challenge": challenge, "code": pyotp.TOTP(secret).at(now)}
    )
    assert enable.status_code == 200
    now += timedelta(seconds=60)
    pending = login(client, credentials)
    assert pending.json()["status"] == "mfa_required"
    assert "access_token" not in pending.json() and "Set-Cookie" not in pending.headers
    otp = pyotp.TOTP(secret).at(now)
    payload = {"challenge": pending.json()["challenge"]}
    wrong = "000000" if otp != "000000" else "111111"
    invalid = post(client, "/mfa/verify", payload={**payload, "code": wrong})
    assert invalid.status_code == 401 and "Set-Cookie" not in invalid.headers
    signed = post(client, "/mfa/verify", payload={**payload, "code": otp})
    assert signed.status_code == 200
    initial = cookie(signed)
    rotated = post(client, "/refresh", refresh=initial)
    assert rotated.status_code == 200
    replacement = cookie(rotated)
    with auth.session_factory()() as db:
        saved = set(db.scalars(select(RefreshGrant.token_hash)))
        if initial in saved or replacement in saved:
            pytest.fail("Raw refresh credential persisted", pytrace=False)
        assert {digest(initial), digest(replacement)} <= saved
    ended = post(client, "/logout", bearer=rotated.json()["access_token"])
    assert ended.status_code == 204
    assert post(client, "/refresh", refresh=replacement).status_code == 401
    output = capfd.readouterr()
    for sensitive in (secret, initial, replacement, credentials["password"]):
        if sensitive in output.out or sensitive in output.err:
            pytest.fail("Authentication secret leaked to logs", pytrace=False)


def test_rotation_reuse_revokes_session_even_under_concurrency(identity):
    client, _, credentials = identity
    initial = cookie(login(client, credentials))
    barrier = Barrier(2)

    def rotate():
        with TestClient(app, base_url=ORIGIN) as thread_client:
            barrier.wait(timeout=10)
            return post(thread_client, "/refresh", refresh=initial)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: rotate(), range(2)))
    assert sorted(result.status_code for result in results) == [200, 401]
    success = next(result for result in results if result.status_code == 200)
    denied = next(result for result in results if result.status_code == 401)
    assert denied.json()["code"] == "AUTH_REFRESH_REUSED"
    assert post(client, "/refresh", refresh=cookie(success)).status_code == 401
    headers = {"Authorization": f"Bearer {success.json()['access_token']}"}
    assert client.get(BASE + "/me", headers=headers).status_code == 401
    print("Concurrent refresh: one HTTP 200, one HTTP 401 AUTH_REFRESH_REUSED; session revoked")


def test_password_change_and_logout_all_revoke_historical_sessions(identity):
    client, _, credentials = identity
    one = login(client, credentials)
    two = login(client, credentials)
    assert post(client, "/logout-all", bearer=one.json()["access_token"]).status_code == 204
    assert post(client, "/refresh", refresh=cookie(one)).status_code == 401
    assert post(client, "/refresh", refresh=cookie(two)).status_code == 401
    three = login(client, credentials)
    four = login(client, credentials)
    new_password = token_urlsafe(24)
    result = post(
        client,
        "/change-password",
        bearer=four.json()["access_token"],
        payload={"password": credentials["password"], "new_password": new_password},
    )
    assert result.status_code == 204
    assert post(client, "/refresh", refresh=cookie(three)).status_code == 401
    assert post(client, "/refresh", refresh=cookie(four)).status_code == 401
    assert post(client, "/login", payload=credentials).status_code == 401
    login(client, {**credentials, "password": new_password})


@pytest.mark.parametrize(
    "path",
    [
        "/refresh",
        "/logout",
        "/logout-all",
        "/change-password",
        "/mfa/setup",
        "/mfa/enable",
        "/mfa/disable",
    ],
)
def test_cookie_endpoint_rejects_cross_origin_and_untrusted_host(identity, path):
    client, _, _ = identity
    for headers in (
        {"Origin": "https://foreign.example.test"},
        {"Origin": ORIGIN, "Host": "foreign.example.test"},
        {},
    ):
        response = client.post(BASE + path, json={}, headers=headers)
        assert response.status_code == 403
        assert response.json()["code"] == "AUTH_ORIGIN_REJECTED"
