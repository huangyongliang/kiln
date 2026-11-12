"""Local login and the start of 用户信息同步.

Login, logout, the OIDC callback, and the sync probe are outside the
auth gate. They do not write domain rows except sessions and the account
created or updated for an external subject. `GET /auth/me` is the only
route on this router that requires a session or the machine token, and it
only reads the current principal.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time

import httpx
from fastapi import APIRouter, Depends, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, RedirectResponse

from kiln.api.deps import get_oidc_http, get_settings, get_store
from kiln.auth import SESSION_COOKIE, Principal, digest_token, require_auth, token_matches
from kiln.config import Settings
from kiln.db.store import PostgresStore
from kiln.domain.accounts import Account
from kiln.domain.auth import LoginRequest, SessionLogin, SessionPrincipal, SyncStatus
from kiln.domain.rbac import PERMISSIONS
from kiln.errors import NotConfigured, Unauthorized, UpstreamFailure
from kiln.oidc import OidcFlowError, begin_authorization, finish_authorization

router = APIRouter(prefix="/auth", tags=["登录"])

OIDC_COOKIE = "kiln_oidc"
_OIDC_COOKIE_PATH = "/api/v1/auth/oidc"
_OIDC_MAX_AGE = 600


@router.post("/login", response_model=SessionLogin, summary="本地口令登录")
def login(
    payload: LoginRequest,
    store: PostgresStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    account, reason = store.authenticate_password(payload.username, payload.password)
    if account is None:
        if reason == "disabled":
            raise Unauthorized("账号已停用")
        raise Unauthorized("用户名或口令不正确")
    body, token = _issue_session(store, settings, account)
    response = JSONResponse(content=jsonable_encoder(body))
    _remember_session(response, settings, token)
    return response


@router.get("/me", response_model=SessionPrincipal, summary="查看当前登录身份")
def current_principal(
    response: Response,
    principal: Principal = Depends(require_auth),
) -> SessionPrincipal:
    response.headers["Cache-Control"] = "no-store"
    return SessionPrincipal(
        kind=principal.kind,
        account=principal.account,
        permissions=[item.code for item in PERMISSIONS if item.code in principal.permissions],
    )


@router.post("/logout", status_code=204, summary="退出并撤销会话")
def logout(
    request: Request,
    store: PostgresStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
) -> Response:
    cookie = request.cookies.get(SESSION_COOKIE, "")
    bearer = _authorization_bearer(request)
    if cookie:
        store.revoke_session_hash(digest_token(cookie))
    if bearer and bearer != cookie and not token_matches(bearer, settings.api_token_value()):
        store.revoke_session_hash(digest_token(bearer))
    response = Response(status_code=204)
    _forget_session(response, settings)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/sync", response_model=SyncStatus, summary="用户信息同步是否已配置")
def sync_status(settings: Settings = Depends(get_settings)) -> SyncStatus:
    return SyncStatus(configured=settings.oidc_configured)


@router.get("/oidc/start", summary="开始用户信息同步登录", response_model=None)
def oidc_start(
    settings: Settings = Depends(get_settings),
    http: httpx.Client = Depends(get_oidc_http),
) -> RedirectResponse:
    _require_sync(settings)
    try:
        started = begin_authorization(settings, http)
    except OidcFlowError as exc:
        _raise_flow(exc)
    response = RedirectResponse(started.url, status_code=302)
    response.set_cookie(
        OIDC_COOKIE,
        _seal(
            {
                "state": started.state,
                "nonce": started.nonce,
                "verifier": started.verifier,
                "exp": int(time.time()) + _OIDC_MAX_AGE,
            },
            settings.api_token_value(),
        ),
        max_age=_OIDC_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=settings.kiln_session_cookie_secure,
        path=_OIDC_COOKIE_PATH,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/oidc/callback", summary="完成用户信息同步登录", response_model=None)
def oidc_callback(
    request: Request,
    store: PostgresStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
    http: httpx.Client = Depends(get_oidc_http),
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> Response:
    _require_sync(settings)
    sealed = _open_seal(request.cookies.get(OIDC_COOKIE, ""), settings.api_token_value())
    expected_state = str(sealed.get("state", "")) if sealed is not None else ""
    if (
        error
        or not code
        or not state
        or sealed is None
        or len(expected_state) != len(state)
        or not hmac.compare_digest(expected_state, state)
    ):
        return _reject_callback(settings)
    try:
        identity = finish_authorization(
            settings,
            http,
            code=code,
            verifier=str(sealed.get("verifier", "")),
            nonce=str(sealed.get("nonce", "")),
        )
    except OidcFlowError as exc:
        if exc.kind == "unavailable":
            raise UpstreamFailure(exc.detail) from None
        return _deny_callback(settings, exc.detail)
    link = store.link_synced_account(
        issuer=identity.issuer,
        subject=identity.subject,
        username_seed=identity.username_seed,
        display_name=identity.display_name,
        email=identity.email,
        employment_status=identity.employment_status,
        employment_active=identity.employment_active,
    )
    if link.account is None:
        detail = "账号已停用" if link.denial == "disabled" else "在职状态未通过"
        return _deny_callback(settings, detail)
    body, token = _issue_session(store, settings, link.account)
    if "text/html" in request.headers.get("accept", "").lower():
        response: Response = RedirectResponse("/", status_code=303)
    else:
        response = JSONResponse(content=jsonable_encoder(body))
    _remember_session(response, settings, token)
    _forget_oidc(response, settings)
    return response


def _issue_session(
    store: PostgresStore,
    settings: Settings,
    account: Account,
) -> tuple[SessionLogin, str]:
    token = secrets.token_urlsafe(32)
    expires_at = store.create_session(
        account.id,
        digest_token(token),
        settings.kiln_session_ttl_seconds,
    )
    return SessionLogin(token=token, expires_at=expires_at, account=account), token


def _remember_session(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.kiln_session_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.kiln_session_cookie_secure,
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"


def _forget_session(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.kiln_session_cookie_secure,
    )


def _forget_oidc(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        OIDC_COOKIE,
        path=_OIDC_COOKIE_PATH,
        httponly=True,
        samesite="lax",
        secure=settings.kiln_session_cookie_secure,
    )


def _reject_callback(settings: Settings) -> Response:
    return _deny_callback(settings, "登录未完成")


def _deny_callback(settings: Settings, detail: str) -> Response:
    response = JSONResponse(status_code=401, content={"detail": detail})
    _forget_oidc(response, settings)
    response.headers["Cache-Control"] = "no-store"
    response.headers["WWW-Authenticate"] = "Bearer"
    return response


def _require_sync(settings: Settings) -> None:
    if not settings.oidc_configured:
        raise NotConfigured("用户信息同步未配置")


def _raise_flow(exc: OidcFlowError) -> None:
    if exc.kind == "unavailable":
        raise UpstreamFailure(exc.detail) from None
    raise Unauthorized(exc.detail) from None


def _authorization_bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return ""


def _seal(payload: dict[str, object], secret: str) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    mac = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    body = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"{body}.{mac}"


def _open_seal(value: str, secret: str) -> dict[str, object] | None:
    if not value or "." not in value:
        return None
    body, mac = value.rsplit(".", 1)
    padded = body + "=" * (-len(body) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    except ValueError:
        return None
    expected = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, mac):
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    exp = payload.get("exp")
    if not isinstance(exp, int) or exp < int(time.time()):
        return None
    return payload
