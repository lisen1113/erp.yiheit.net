"""Authentication endpoints with serialized account and session changes."""

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from ipaddress import ip_address
from secrets import token_urlsafe
from typing import Annotated
from uuid import UUID

import jwt
import pyotp
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from yihe_api.audit.service import Actor, Category, Source, audit_change, stamp_identity
from yihe_api.auth.csrf import allows_cookie_request
from yihe_api.auth.models import AuthSession, Employee, LoginChallenge, RefreshGrant, User
from yihe_api.auth.schemas import (
    Access,
    Challenge,
    ChallengeVerify,
    ConfirmPassword,
    CurrentUser,
    Login,
    PasswordChange,
    Setup,
    TotpCode,
)
from yihe_api.auth.security import (
    ACCESS_SECONDS,
    CHALLENGE_SECONDS,
    ISSUER,
    LOCK_WINDOW,
    PASSWORD_HASHER,
    SESSION_SECONDS,
    access_token,
    auth_config,
    check_password,
    digest,
    dummy_hash,
    valid_new_password,
    verify_totp,
)
from yihe_api.db.models import Company
from yihe_api.db.session import DatabaseSettings, make_engine, make_session_factory
from yihe_api.errors import ApiError, ErrorCode
from yihe_api.tracing import LOGGER, current_trace_id

router = APIRouter(tags=["authentication"])
COOKIE = "__Secure-erp_refresh"
COOKIE_PATH = "/api/v1/auth"


def now_utc() -> datetime:
    return datetime.now(UTC)


@lru_cache
def session_factory() -> sessionmaker[Session]:
    return make_session_factory(make_engine(DatabaseSettings.from_environment()))


def database() -> Iterator[Session]:
    with session_factory()() as session, session.begin():
        yield session


DB = Annotated[Session, Depends(database, scope="function")]


def csrf(request: Request, response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    if not allows_cookie_request(
        trusted=auth_config().origin,
        origins=request.headers.getlist("origin"),
        hosts=request.headers.getlist("host"),
    ):
        raise ApiError(ErrorCode.AUTH_ORIGIN_REJECTED)


def locked_user(db: Session, user_id: UUID) -> User | None:
    return db.get(User, user_id, with_for_update=True)


def available(db: Session, user: User, now: datetime) -> bool:
    return (
        user.status == "active"
        and (user.locked_until is None or user.locked_until <= now)
        and bool(db.scalar(select(Employee.is_active).where(Employee.id == user.employee_id)))
        and db.scalar(select(Company.is_deleted).where(Company.id == user.company_id)) is False
    )


def note_failure(request: Request) -> None:
    host = request.client.host if request.client else ""
    try:
        ip = str(ip_address(host))
    except ValueError:
        ip = None
    agent = request.headers.get("user-agent", "")[:512]
    LOGGER.warning(
        "auth.login_failure",
        extra={
            "client_ip": ip,
            "agent_digest": hashlib.sha256(agent.encode()).hexdigest(),
            "auth_trace": current_trace_id(),
        },
    )


def failed_attempt(db: Session, user: User, now: datetime) -> None:
    if user.failure_window_at is None or user.failure_window_at <= now - LOCK_WINDOW:
        user.failure_window_at = now
        user.failed_login_count = 0
    user.failed_login_count += 1
    if user.failed_login_count >= 5:
        user.locked_until = now + LOCK_WINDOW
        revoke_all(db, user, now)
        audit_change(
            db,
            company_id=user.company_id,
            action="auth.account_locked",
            entity_type="app_user",
            category=Category.SECURITY,
            entity_id=user.id,
            source=Source.SYSTEM,
        )
    # Persist security mutations before the error leaves the transaction dependency.
    db.commit()


def revoke_all(db: Session, user: User, now: datetime) -> None:
    db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    db.execute(
        update(LoginChallenge)
        .where(LoginChallenge.user_id == user.id, LoginChallenge.consumed.is_(False))
        .values(consumed=True)
    )


def clear_failures(user: User) -> None:
    user.failed_login_count = 0
    user.failure_window_at = None
    user.locked_until = None


def set_refresh(response: Response, db: Session, session: AuthSession, now: datetime) -> None:
    token = token_urlsafe(48)
    db.add(RefreshGrant(token_hash=digest(token), session_id=session.id))
    response.set_cookie(
        COOKIE,
        token,
        max_age=int((session.expires_at - now).total_seconds()),
        path=COOKIE_PATH,
        secure=True,
        httponly=True,
        samesite="lax",
    )


def clear_refresh(response: Response) -> None:
    response.delete_cookie(COOKIE, path=COOKIE_PATH, secure=True, httponly=True, samesite="lax")


def access(user: User, session: AuthSession, now: datetime) -> Access:
    return Access(
        access_token=access_token(
            auth_config(),
            user=str(user.id),
            company=str(user.company_id),
            session=str(session.id),
            now=now,
        ),
        expires_in=ACCESS_SECONDS,
    )


def begin_session(
    db: Session, user: User, response: Response, now: datetime, request: Request
) -> Access:
    clear_failures(user)
    user.last_login_at = now
    session = AuthSession(
        company_id=user.company_id,
        user_id=user.id,
        expires_at=now + timedelta(seconds=SESSION_SECONDS),
        mfa_verified=user.mfa_enabled,
    )
    db.add(session)
    db.flush()
    set_refresh(response, db, session, now)
    stamp_identity(request, Actor(user.company_id, user.id, user.employee_id, session.id))
    auth_audit(db, request, user, "auth.login")
    return access(user, session, now)


def authenticated(request: Request, db: Session, *, lock: bool = True) -> tuple[User, AuthSession]:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token or len(token) > 4096:
        raise ApiError(ErrorCode.UNAUTHENTICATED)
    try:
        claims = jwt.decode(
            token,
            auth_config().signing_key,
            algorithms=["HS256"],
            issuer=ISSUER,
            options={
                "require": [
                    "iss",
                    "sub",
                    "user_id",
                    "company_id",
                    "session_id",
                    "iat",
                    "exp",
                    "jti",
                ]
            },
        )
        user_id = UUID(claims["sub"])
        session_id = UUID(claims["session_id"])
        company_id = UUID(claims["company_id"])
        if claims["user_id"] != claims["sub"]:
            raise ValueError
    except (jwt.InvalidTokenError, ValueError, TypeError, AttributeError):
        raise ApiError(ErrorCode.UNAUTHENTICATED) from None
    user = db.get(User, user_id, with_for_update=lock, populate_existing=True)
    session = db.get(AuthSession, session_id, with_for_update=lock, populate_existing=True)
    now = now_utc()
    if (
        user is None
        or user.company_id != company_id
        or not available(db, user, now)
        or session is None
        or session.user_id != user.id
        or session.revoked_at is not None
        or session.expires_at <= now
        or (user.mfa_required and not user.mfa_enabled)
    ):
        raise ApiError(ErrorCode.UNAUTHENTICATED)
    stamp_identity(request, Actor(user.company_id, user.id, user.employee_id, session.id))
    return user, session


def challenge_user(db: Session, token: str, now: datetime) -> tuple[User, LoginChallenge]:
    key = digest(token)
    user_id = db.scalar(select(LoginChallenge.user_id).where(LoginChallenge.token_hash == key))
    user = locked_user(db, user_id) if user_id else None
    challenge = db.get(LoginChallenge, key, with_for_update=True)
    if (
        user is None
        or challenge is None
        or challenge.consumed
        or challenge.expires_at <= now
        or not available(db, user, now)
    ):
        raise ApiError(ErrorCode.UNAUTHENTICATED)
    return user, challenge


@router.post("/login", dependencies=[Depends(csrf)])
def login(payload: Login, request: Request, response: Response, db: DB) -> Access | Challenge:
    auth_config()
    now = now_utc()
    user = db.scalar(
        select(User)
        .where(User.username_normalized == func.lower(payload.username))
        .with_for_update()
    )
    request.state.audit_identifier = payload.username
    password = payload.password.get_secret_value()
    usable = user is not None and available(db, user, now)
    encoded = user.password_hash if user is not None and usable else dummy_hash()
    matches = check_password(encoded, password)
    if user is None or not usable or not matches:
        note_failure(request)
        if user is not None and usable:
            failed_attempt(db, user, now)
        raise ApiError(ErrorCode.AUTH_INVALID_CREDENTIALS)
    if user.mfa_enabled or user.mfa_required:
        token = token_urlsafe(48)
        db.add(
            LoginChallenge(
                token_hash=digest(token),
                user_id=user.id,
                expires_at=now + timedelta(seconds=CHALLENGE_SECONDS),
            )
        )
        return Challenge(
            status="mfa_required" if user.mfa_enabled else "mfa_setup_required",
            challenge=token,
            expires_in=CHALLENGE_SECONDS,
        )
    return begin_session(db, user, response, now, request)


def verify_code(db: Session, user: User, code: str, now: datetime, request: Request) -> None:
    encrypted = user.mfa_secret_encrypted
    step = (
        verify_totp(auth_config(), encrypted, code, now, user.mfa_last_step) if encrypted else None
    )
    if step is None:
        note_failure(request)
        failed_attempt(db, user, now)
        raise ApiError(ErrorCode.AUTH_INVALID_MFA)
    user.mfa_last_step = step


@router.post("/mfa/verify", dependencies=[Depends(csrf)])
def mfa_verify(payload: ChallengeVerify, request: Request, response: Response, db: DB) -> Access:
    now = now_utc()
    user, challenge = challenge_user(db, payload.challenge.get_secret_value(), now)
    if not user.mfa_enabled:
        raise ApiError(ErrorCode.AUTH_INVALID_MFA)
    verify_code(db, user, payload.code, now, request)
    challenge.consumed = True
    auth_audit(db, request, user, "auth.mfa_verify", {"mfa_event": "verified"})
    return begin_session(db, user, response, now, request)


@router.post("/refresh", dependencies=[Depends(csrf)])
def refresh(request: Request, response: Response, db: DB) -> Access:
    token = request.cookies.get(COOKIE)
    if not token or len(token) > 128:
        raise ApiError(ErrorCode.UNAUTHENTICATED)
    key = digest(token)
    owner = db.execute(
        select(AuthSession.user_id, AuthSession.id)
        .join(RefreshGrant, RefreshGrant.session_id == AuthSession.id)
        .where(RefreshGrant.token_hash == key)
    ).one_or_none()
    if owner is None:
        raise ApiError(ErrorCode.UNAUTHENTICATED)
    user_id, session_id = owner
    user = locked_user(db, user_id)
    session = db.get(AuthSession, session_id, with_for_update=True)
    grant = db.get(RefreshGrant, key, with_for_update=True)
    now = now_utc()
    if user is None or session is None or grant is None:
        raise ApiError(ErrorCode.UNAUTHENTICATED)
    if grant.consumed_at is not None:
        if session.revoked_at is None:
            session.revoked_at = now
        db.commit()
        raise ApiError(ErrorCode.AUTH_REFRESH_REUSED)
    if (
        session.revoked_at is not None
        or session.expires_at <= now
        or not available(db, user, now)
        or (user.mfa_required and not user.mfa_enabled)
    ):
        raise ApiError(ErrorCode.UNAUTHENTICATED)
    grant.consumed_at = now
    session.last_used_at = now
    set_refresh(response, db, session, now)
    return access(user, session, now)


@router.post("/logout", status_code=204, dependencies=[Depends(csrf)])
def logout(request: Request, response: Response, db: DB) -> None:
    user, session = authenticated(request, db)
    session.revoked_at = now_utc()
    auth_audit(db, request, user, "auth.logout")
    clear_refresh(response)


@router.post("/logout-all", status_code=204, dependencies=[Depends(csrf)])
def logout_all(request: Request, response: Response, db: DB) -> None:
    user, _ = authenticated(request, db)
    revoke_all(db, user, now_utc())
    auth_audit(db, request, user, "auth.logout_all")
    clear_refresh(response)


@router.get("/me")
def me(request: Request, response: Response, db: DB) -> CurrentUser:
    response.headers["Cache-Control"] = "no-store"
    user, _ = authenticated(request, db)
    return CurrentUser(
        user_id=user.id,
        company_id=user.company_id,
        employee_id=user.employee_id,
        username=user.username,
        mfa_required=user.mfa_required,
        mfa_enabled=user.mfa_enabled,
    )


def reauthenticate(db: Session, user: User, password: str, request: Request) -> None:
    if not check_password(user.password_hash, password):
        note_failure(request)
        failed_attempt(db, user, now_utc())
        raise ApiError(ErrorCode.AUTH_INVALID_CREDENTIALS)


@router.post("/change-password", status_code=204, dependencies=[Depends(csrf)])
def change_password(payload: PasswordChange, request: Request, response: Response, db: DB) -> None:
    user, _ = authenticated(request, db)
    reauthenticate(db, user, payload.password.get_secret_value(), request)
    password = payload.new_password.get_secret_value()
    if not valid_new_password(password, user.username):
        raise ApiError(ErrorCode.AUTH_PASSWORD_POLICY)
    now = now_utc()
    user.password_hash = PASSWORD_HASHER.hash(password)
    user.password_changed_at = now
    auth_audit(db, request, user, "auth.password_change", {"password_changed": True})
    revoke_all(db, user, now)
    clear_refresh(response)


@router.post("/mfa/setup", dependencies=[Depends(csrf)])
def mfa_setup(payload: ConfirmPassword, request: Request, db: DB) -> Setup:
    now = now_utc()
    if payload.challenge is not None:
        user, _ = challenge_user(db, payload.challenge.get_secret_value(), now)
    else:
        user, _ = authenticated(request, db)
    reauthenticate(db, user, payload.password.get_secret_value(), request)
    if user.mfa_enabled:
        raise ApiError(ErrorCode.CONFLICT)
    secret = pyotp.random_base32()
    user.mfa_secret_encrypted = auth_config().cipher.encrypt(secret.encode()).decode()
    user.mfa_setup_expires_at = now + timedelta(seconds=CHALLENGE_SECONDS)
    user.mfa_last_step = None
    auth_audit(db, request, user, "auth.mfa_setup", {"mfa_event": "setup"})
    return Setup(
        otpauth_uri=pyotp.TOTP(secret).provisioning_uri(
            name=user.username, issuer_name="上海以和 ERP"
        )
    )


@router.post("/mfa/enable", dependencies=[Depends(csrf)])
def mfa_enable(payload: TotpCode, request: Request, response: Response, db: DB) -> Access:
    now = now_utc()
    if payload.challenge is not None:
        user, _ = challenge_user(db, payload.challenge.get_secret_value(), now)
    else:
        user, _ = authenticated(request, db)
    if user.mfa_enabled or user.mfa_setup_expires_at is None or user.mfa_setup_expires_at <= now:
        raise ApiError(ErrorCode.CONFLICT)
    verify_code(db, user, payload.code, now, request)
    user.mfa_enabled = True
    auth_audit(db, request, user, "auth.mfa_enable", {"mfa_event": "enabled"})
    user.mfa_setup_expires_at = None
    revoke_all(db, user, now)
    return begin_session(db, user, response, now, request)


@router.post("/mfa/disable", status_code=204, dependencies=[Depends(csrf)])
def mfa_disable(payload: ConfirmPassword, request: Request, response: Response, db: DB) -> None:
    user, _ = authenticated(request, db)
    reauthenticate(db, user, payload.password.get_secret_value(), request)
    if user.mfa_required:
        raise ApiError(ErrorCode.FORBIDDEN)
    user.mfa_enabled = False
    user.mfa_secret_encrypted = None
    user.mfa_last_step = None
    user.mfa_setup_expires_at = None
    revoke_all(db, user, now_utc())
    auth_audit(db, request, user, "auth.mfa_disable", {"mfa_event": "disabled"})
    clear_refresh(response)


def auth_audit(
    db: Session,
    request: Request,
    user: User,
    action: str,
    metadata: dict[str, object] | None = None,
) -> None:
    context = getattr(request.state, "audit_actor", None)
    actor = context or Actor(user.company_id, user.id, user.employee_id)
    audit_change(
        db,
        company_id=user.company_id,
        actor=actor,
        entity_type="app_user",
        entity_id=user.id,
        action=action,
        category=Category.AUTHENTICATION,
        metadata=metadata,
        request=request,
    )
