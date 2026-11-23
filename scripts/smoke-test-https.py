"""Exercise the deployed HTTPS auth boundary using only T010 synthetic identities."""

import http.cookiejar
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pyotp

ORIGIN = "https://erp.yiheit.net"
PASSWORD = Path(os.environ["T011_SEED_PASSWORD_FILE"]).read_text().strip()


def client():
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )


def call(opener, path, expected, *, body=None, method="POST", origin=ORIGIN, token=None):
    headers = {"Content-Type": "application/json"}
    if origin is not None:
        headers["Origin"] = origin
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        ORIGIN + "/api/v1/" + path,
        data=None if body is None else json.dumps(body).encode(),
        method=method,
        headers=headers,
    )
    try:
        response = opener.open(req, timeout=15)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        if response.status != expected:
            raise SystemExit(f"{method} {path}: expected {expected}, received {response.status}")
        payload = response.read()
        return (json.loads(payload) if payload else {}), response.headers


browser = client()
login, headers = call(browser, "auth/login", 200, body={"username": "demo-e005", "password": PASSWORD})
token = login["access_token"]
cookie = headers.get("Set-Cookie", "").lower()
for attribute in ("secure", "httponly", "samesite=lax", "path=/api/v1/auth"):
    if attribute not in {part.strip() for part in cookie.split(";")[1:]}:
        raise SystemExit(f"Refresh cookie missing {attribute}")
call(browser, "auth/refresh", 403, origin=None)
call(browser, "auth/refresh", 403, origin="https://invalid.example")
rotated, _ = call(browser, "auth/refresh", 200)
token = rotated["access_token"]
call(browser, "auth/me", 200, method="GET", token=token)
call(browser, "security-events", 403, method="GET", token=token)
call(browser, "auth/logout", 204, token=token)
call(browser, "auth/me", 401, method="GET", token=token)

# This one-time enrollment uses the dedicated test administrator on bootstrap.
if os.environ.get("T011_BOOTSTRAP_HTTPS") == "true":
    admin = client()
    login, _ = call(admin, "auth/login", 200, body={"username": "demo-e001", "password": PASSWORD})
    if login["status"] != "mfa_setup_required":
        raise SystemExit("Seed administrator did not require MFA enrollment")
    challenge = login["challenge"]
    setup, _ = call(admin, "auth/mfa/setup", 200, body={"password": PASSWORD, "challenge": challenge})
    secret = urllib.parse.parse_qs(urllib.parse.urlsplit(setup["otpauth_uri"]).query)["secret"][0]
    mfa, _ = call(admin, "auth/mfa/enable", 200, body={"code": pyotp.TOTP(secret).now(), "challenge": challenge})
    token = mfa["access_token"]
    call(admin, "audit-logs", 403, method="GET", token=token)
    call(admin, "security-events", 200, method="GET", token=token)
    call(admin, "auth/logout", 204, token=token)

auditor = client()
login, _ = call(auditor, "auth/login", 200, body={"username": "demo-e009", "password": PASSWORD})
token = login["access_token"]
call(auditor, "audit-logs", 200, method="GET", token=token)
call(auditor, "security-events", 403, method="GET", token=token)
call(auditor, "auth/logout", 204, token=token)

print("HTTPS login, cookie flags, refresh, CSRF, RBAC and logout passed.")
if os.environ.get("T011_BOOTSTRAP_HTTPS") == "true":
    print("Test administrator MFA enrollment and audit/security query passed.")
