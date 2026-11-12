"""Authenticate `/api/v1`.

Order: user session cookie, user bearer token, then `KILN_API_TOKEN`.
The machine token stays a required dev credential and has every catalog
permission. Optional OIDC is only the technical mechanism for 用户信息同步.
"""

import hashlib
import hmac
from dataclasses import dataclass
from typing import Literal

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from kiln.config import Settings
from kiln.domain.accounts import Account
from kiln.domain.rbac import ALL_PERMISSIONS, permissions_for
from kiln.errors import Forbidden, Unauthorized

SESSION_COOKIE = "kiln_session"

_bearer = HTTPBearer(
    auto_error=False,
    description="用户会话令牌，或机器访问令牌 KILN_API_TOKEN",
)
_PRINCIPAL = "kiln_principal"


@dataclass(frozen=True)
class Principal:
    kind: Literal["machine", "user"]
    permissions: frozenset[str]
    account: Account | None = None


def token_matches(provided: str, expected: str) -> bool:
    return hmac.compare_digest(
        hashlib.sha256(provided.encode("utf-8")).digest(),
        hashlib.sha256(expected.encode("utf-8")).digest(),
    )


def digest_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def require_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    cached = getattr(request.state, _PRINCIPAL, None)
    if isinstance(cached, Principal):
        return cached
    principal = _resolve_principal(request, credentials)
    setattr(request.state, _PRINCIPAL, principal)
    return principal


def require_permission(code: str):
    if code not in ALL_PERMISSIONS:
        raise RuntimeError(f"unknown permission: {code}")

    def checker(principal: Principal = Depends(require_auth)) -> Principal:
        if code not in principal.permissions:
            raise Forbidden()
        return principal

    return checker


def _resolve_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
) -> Principal:
    settings: Settings = request.app.state.settings
    store = request.app.state.store
    cookie = request.cookies.get(SESSION_COOKIE, "")
    if cookie:
        account = store.account_for_session_hash(digest_token(cookie))
        if account is not None:
            return _user_principal(account)
    bearer = _bearer_value(credentials)
    if bearer:
        account = store.account_for_session_hash(digest_token(bearer))
        if account is not None:
            return _user_principal(account)
        if token_matches(bearer, settings.api_token_value()):
            return Principal(kind="machine", permissions=ALL_PERMISSIONS)
    raise Unauthorized()


def _user_principal(account: Account) -> Principal:
    return Principal(
        kind="user",
        permissions=permissions_for(account.role_ids),
        account=account,
    )


def _bearer_value(credentials: HTTPAuthorizationCredentials | None) -> str:
    if credentials is not None and credentials.scheme.lower() == "bearer":
        return credentials.credentials
    return ""
