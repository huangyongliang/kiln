"""Local bearer token for the HTTP API.

This is a single shared gate, not login, passwords, or identity federation.
`KILN_API_TOKEN` defaults to a local-only value. Callers without that token
cannot read or change domain data. `/health`, `/`, and the OpenAPI pages do
not accept writes.
"""

import hashlib
import hmac

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from kiln.config import Settings
from kiln.errors import Unauthorized

_bearer = HTTPBearer(auto_error=False, description="本地访问令牌，取自 KILN_API_TOKEN")


def token_matches(provided: str, expected: str) -> bool:
    return hmac.compare_digest(
        hashlib.sha256(provided.encode("utf-8")).digest(),
        hashlib.sha256(expected.encode("utf-8")).digest(),
    )


def require_api_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    settings: Settings = request.app.state.settings
    provided = ""
    if credentials is not None and credentials.scheme.lower() == "bearer":
        provided = credentials.credentials
    if not token_matches(provided, settings.api_token_value()):
        raise Unauthorized()
