"""Optional OIDC authorization-code + PKCE client for 用户信息同步.

The company user system, when configured, supplies identity and employment
status. Kiln accounts and kiln permissions stay on the platform. Company
groups and roles are not copied.

HTTP calls use Authlib's sync OAuth2 client. If that client rejects an
http://127.0.0.1 authorize or token URL, PKCE and token preparation stay in
Authlib and the request itself uses kiln's httpx client.
"""

import warnings
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

import authlib.deprecate
import httpx
from joserfc.errors import InvalidClaimError, JoseError, MissingClaimError

from kiln.config import Settings
from kiln.domain.accounts import AccountCreate, username_seed

warnings.filterwarnings("ignore", category=authlib.deprecate.AuthlibDeprecationWarning)

from authlib.common.security import generate_token  # noqa: E402
from authlib.integrations.base_client.errors import OAuthError  # noqa: E402
from authlib.integrations.httpx_client import OAuth2Client  # noqa: E402
from authlib.jose import JsonWebKey, JsonWebToken  # noqa: E402
from authlib.oauth2.rfc6749.errors import InsecureTransportError  # noqa: E402
from authlib.oauth2.rfc6749.parameters import prepare_grant_uri  # noqa: E402
from authlib.oauth2.rfc7636 import create_s256_code_challenge  # noqa: E402
from authlib.oidc.core import CodeIDToken  # noqa: E402

_JWT = JsonWebToken(["RS256"])


class OidcFlowError(Exception):
    def __init__(self, kind: Literal["unavailable", "rejected"], detail: str) -> None:
        self.kind = kind
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True)
class AuthorizationRequest:
    url: str
    state: str
    nonce: str
    verifier: str


@dataclass(frozen=True)
class SyncedIdentity:
    issuer: str
    subject: str
    username_seed: str
    display_name: str
    email: str | None
    employment_status: str | None
    employment_active: bool


def begin_authorization(settings: Settings, http: httpx.Client) -> AuthorizationRequest:
    metadata = load_provider(settings, http)
    verifier = generate_token(48)
    nonce = generate_token(24)
    state = generate_token(24)
    url = _authorization_url(
        settings,
        metadata["authorization_endpoint"],
        verifier,
        nonce,
        state,
    )
    return AuthorizationRequest(url=url, state=state, nonce=nonce, verifier=verifier)


def finish_authorization(
    settings: Settings,
    http: httpx.Client,
    *,
    code: str,
    verifier: str,
    nonce: str,
) -> SyncedIdentity:
    metadata = load_provider(settings, http)
    token = _exchange_code(settings, http, metadata["token_endpoint"], code, verifier)
    id_token = token.get("id_token")
    access_token = token.get("access_token")
    if not isinstance(id_token, str) or not id_token:
        raise OidcFlowError("rejected", "登录未完成")
    if not isinstance(access_token, str) or not access_token:
        raise OidcFlowError("rejected", "登录未完成")
    claims = _validate_id_token(
        settings,
        http,
        metadata["jwks_uri"],
        id_token,
        nonce,
        access_token,
    )
    userinfo = _userinfo(http, metadata["userinfo_endpoint"], access_token)
    if str(userinfo.get("sub", "")) != str(claims.get("sub", "")):
        raise OidcFlowError("rejected", "登录未完成")
    return _identity(settings, claims, userinfo)


def load_provider(settings: Settings, http: httpx.Client) -> dict[str, str]:
    issuer = settings.kiln_oidc_issuer_url
    try:
        response = http.get(f"{issuer}/.well-known/openid-configuration")
        response.raise_for_status()
        document = response.json()
    except (httpx.HTTPError, ValueError):
        raise OidcFlowError("unavailable", "用户信息同步暂时不可用") from None
    if not isinstance(document, dict):
        raise OidcFlowError("unavailable", "用户信息同步暂时不可用")
    discovered = str(document.get("issuer", "")).rstrip("/")
    if discovered != issuer:
        raise OidcFlowError("unavailable", "用户信息同步暂时不可用")
    metadata: dict[str, str] = {}
    for key in (
        "authorization_endpoint",
        "token_endpoint",
        "userinfo_endpoint",
        "jwks_uri",
    ):
        metadata[key] = _http_url(document.get(key))
    return metadata


def _authorization_url(
    settings: Settings,
    authorization_endpoint: str,
    verifier: str,
    nonce: str,
    state: str,
) -> str:
    try:
        with _oauth_client(settings) as client:
            url, _state = client.create_authorization_url(
                authorization_endpoint,
                state=state,
                code_verifier=verifier,
                nonce=nonce,
            )
        return url
    except InsecureTransportError:
        return prepare_grant_uri(
            authorization_endpoint,
            client_id=settings.kiln_oidc_client_id,
            response_type="code",
            redirect_uri=settings.kiln_oidc_redirect_uri,
            scope=settings.oidc_scopes(),
            state=state,
            nonce=nonce,
            code_challenge=create_s256_code_challenge(verifier),
            code_challenge_method="S256",
        )


def _exchange_code(
    settings: Settings,
    http: httpx.Client,
    token_endpoint: str,
    code: str,
    verifier: str,
) -> dict[str, object]:
    token: dict[str, object] | None = None
    try:
        with _oauth_client(settings) as client:
            try:
                fetched = client.fetch_token(
                    token_endpoint,
                    grant_type="authorization_code",
                    code=code,
                    code_verifier=verifier,
                )
            except InsecureTransportError:
                fetched = None
        if fetched is not None:
            token = dict(fetched)
    except OAuthError:
        raise OidcFlowError("rejected", "登录未完成") from None
    except httpx.HTTPError:
        raise OidcFlowError("unavailable", "用户信息同步暂时不可用") from None
    if token is None:
        token = _exchange_with_httpx(settings, http, token_endpoint, code, verifier)
    return token


def _exchange_with_httpx(
    settings: Settings,
    http: httpx.Client,
    token_endpoint: str,
    code: str,
    verifier: str,
) -> dict[str, object]:
    try:
        response = http.post(
            token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.kiln_oidc_redirect_uri,
                "client_id": settings.kiln_oidc_client_id,
                "client_secret": settings.oidc_client_secret_value(),
                "code_verifier": verifier,
            },
        )
    except httpx.HTTPError:
        raise OidcFlowError("unavailable", "用户信息同步暂时不可用") from None
    if response.status_code != 200:
        raise OidcFlowError("rejected", "登录未完成")
    try:
        payload = response.json()
    except ValueError:
        raise OidcFlowError("rejected", "登录未完成") from None
    if not isinstance(payload, dict):
        raise OidcFlowError("rejected", "登录未完成")
    return payload


def _validate_id_token(
    settings: Settings,
    http: httpx.Client,
    jwks_uri: str,
    id_token: str,
    nonce: str,
    access_token: str,
) -> dict[str, object]:
    try:
        response = http.get(jwks_uri)
        response.raise_for_status()
        jwks = response.json()
    except (httpx.HTTPError, ValueError):
        raise OidcFlowError("unavailable", "用户信息同步暂时不可用") from None
    if not isinstance(jwks, dict):
        raise OidcFlowError("rejected", "登录未完成")
    try:
        key_set = JsonWebKey.import_key_set(jwks)
        claims = _JWT.decode(
            id_token,
            key_set,
            claims_cls=CodeIDToken,
            claims_options={
                "iss": {"essential": True, "value": settings.kiln_oidc_issuer_url},
                "sub": {"essential": True},
                "aud": {"essential": True, "value": settings.kiln_oidc_client_id},
                "exp": {"essential": True},
                "iat": {"essential": True},
            },
            claims_params={
                "nonce": nonce,
                "client_id": settings.kiln_oidc_client_id,
                "access_token": access_token,
            },
        )
        claims.validate()
    except (JoseError, InvalidClaimError, MissingClaimError, ValueError):
        raise OidcFlowError("rejected", "登录未完成") from None
    return dict(claims)


def _userinfo(http: httpx.Client, userinfo_endpoint: str, access_token: str) -> dict[str, object]:
    try:
        response = http.get(
            userinfo_endpoint,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        raise OidcFlowError("unavailable", "用户信息同步暂时不可用") from None
    if not isinstance(payload, dict) or not payload.get("sub"):
        raise OidcFlowError("rejected", "登录未完成")
    return payload


def _identity(
    settings: Settings,
    claims: dict[str, object],
    userinfo: dict[str, object],
) -> SyncedIdentity:
    subject = str(claims.get("sub", "")).strip()
    if not subject:
        raise OidcFlowError("rejected", "登录未完成")
    claim_name = settings.kiln_oidc_employment_claim
    id_status = _text_claim(claims, claim_name)
    info_status = _text_claim(userinfo, claim_name)
    if id_status is not None and info_status is not None and id_status != info_status:
        raise OidcFlowError("rejected", "登录未完成")
    employment_status = id_status if id_status is not None else info_status
    active = employment_status in settings.oidc_employment_active_values()
    email = _optional_email(_text_claim(claims, "email") or _text_claim(userinfo, "email"))
    preferred = _text_claim(claims, "preferred_username") or _text_claim(
        userinfo, "preferred_username"
    )
    seed = username_seed(preferred, email, subject)
    display = _display_name(
        _text_claim(claims, "name") or _text_claim(userinfo, "name"),
        seed,
    )
    return SyncedIdentity(
        issuer=settings.kiln_oidc_issuer_url,
        subject=subject,
        username_seed=seed,
        display_name=display,
        email=email,
        employment_status=employment_status,
        employment_active=active,
    )


def _oauth_client(settings: Settings) -> OAuth2Client:
    return OAuth2Client(
        settings.kiln_oidc_client_id,
        settings.oidc_client_secret_value(),
        redirect_uri=settings.kiln_oidc_redirect_uri,
        scope=settings.oidc_scopes(),
        code_challenge_method="S256",
        token_endpoint_auth_method="client_secret_post",
        timeout=30.0,
        trust_env=False,
    )


def _http_url(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise OidcFlowError("unavailable", "用户信息同步暂时不可用")
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise OidcFlowError("unavailable", "用户信息同步暂时不可用")
    return value


def _text_claim(claims: dict[str, object], name: str) -> str | None:
    value = claims.get(name)
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _optional_email(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return AccountCreate.normalize_email(value)
    except ValueError:
        return None


def _display_name(value: str | None, fallback: str) -> str:
    text = "".join(char for char in (value or "") if ord(char) >= 32).strip()
    if not text:
        text = fallback
    return text[:80]
