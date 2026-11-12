"""In-process OIDC authorization-code test double for 用户信息同步.

The double listens on 127.0.0.1 and implements authorize, token, JWKS, and
userinfo, including the employment claim. Tests start it in this process.
It is not a compose service and it does not use an external issuer.
"""

import json
import secrets
import threading
import time
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import authlib.deprecate

warnings.filterwarnings("ignore", category=authlib.deprecate.AuthlibDeprecationWarning)

from authlib.jose import JsonWebKey, JsonWebToken  # noqa: E402
from authlib.oauth2.rfc7636 import create_s256_code_challenge  # noqa: E402

_JWT = JsonWebToken(["RS256"])
_MISSING = object()


class OidcDouble:
    def __init__(self) -> None:
        self.client_id = "kiln-test-client"
        self.client_secret = secrets.token_urlsafe(18)
        self.redirect_uri = "http://127.0.0.1:9/api/v1/auth/oidc/callback"
        self.employment_claim = "employment"
        self.identity: dict[str, Any] = {
            "sub": "person-1",
            "email": "person@example.com",
            "name": "同步用户",
            "preferred_username": "sync_user",
            "employment": "active",
            "groups": ["company-admin"],
            "roles": ["publisher"],
        }
        self.saw_pkce = False
        self.last_id_claims: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._codes: dict[str, dict[str, Any]] = {}
        self._access: dict[str, dict[str, Any]] = {}
        key = JsonWebKey.generate_key("RSA", 2048, is_private=True)
        public = key.as_dict(is_private=False)
        self._key = key
        self._kid = str(public["kid"])
        self._jwks = {"keys": [public]}
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._httpd.double = self  # type: ignore[attr-defined]
        self.port = int(self._httpd.server_address[1])
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def issuer(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> "OidcDouble":
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._httpd.shutdown()
        self._thread.join(timeout=5)
        self._httpd.server_close()

    def issue(self, code: str, challenge: str, redirect_uri: str, nonce: str) -> None:
        with self._lock:
            self._codes[code] = {
                "challenge": challenge,
                "redirect_uri": redirect_uri,
                "nonce": nonce,
                "claims": self._claims(nonce),
            }

    def redeem(
        self,
        *,
        code: str,
        verifier: str,
        redirect_uri: str,
        client_id: str,
        client_secret: str,
    ) -> dict[str, Any] | None:
        if client_id != self.client_id or client_secret != self.client_secret:
            return None
        with self._lock:
            pending = self._codes.pop(code, None)
        if pending is None or pending["redirect_uri"] != redirect_uri:
            return None
        if create_s256_code_challenge(verifier) != pending["challenge"]:
            return None
        self.saw_pkce = True
        claims = pending["claims"]
        self.last_id_claims = claims
        access_token = secrets.token_urlsafe(24)
        with self._lock:
            self._access[access_token] = claims
        id_token = _JWT.encode({"alg": "RS256", "kid": self._kid}, claims, self._key)
        token = id_token.decode("ascii") if isinstance(id_token, bytes) else str(id_token)
        return {
            "access_token": access_token,
            "id_token": token,
            "token_type": "Bearer",
            "expires_in": 300,
        }

    def userinfo(self, access_token: str) -> dict[str, Any] | None:
        with self._lock:
            claims = self._access.get(access_token)
        if claims is None:
            return None
        return dict(claims)

    def _claims(self, nonce: str) -> dict[str, Any]:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": self.issuer,
            "sub": self.identity["sub"],
            "aud": self.client_id,
            "exp": now + 300,
            "iat": now,
            "nonce": nonce,
            "email": self.identity.get("email"),
            "name": self.identity.get("name"),
            "preferred_username": self.identity.get("preferred_username"),
            "groups": list(self.identity.get("groups") or []),
            "roles": list(self.identity.get("roles") or []),
        }
        employment = self.identity.get("employment", _MISSING)
        if employment is not _MISSING and employment is not None:
            claims[self.employment_claim] = employment
        return {key: value for key, value in claims.items() if value is not None}


class _Handler(BaseHTTPRequestHandler):
    server: ThreadingHTTPServer

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        double: OidcDouble = self.server.double  # type: ignore[attr-defined]
        parts = urlsplit(self.path)
        if parts.path == "/.well-known/openid-configuration":
            self._json(
                {
                    "issuer": double.issuer,
                    "authorization_endpoint": f"{double.issuer}/authorize",
                    "token_endpoint": f"{double.issuer}/token",
                    "userinfo_endpoint": f"{double.issuer}/userinfo",
                    "jwks_uri": f"{double.issuer}/jwks",
                    "response_types_supported": ["code"],
                    "subject_types_supported": ["public"],
                    "id_token_signing_alg_values_supported": ["RS256"],
                    "code_challenge_methods_supported": ["S256"],
                }
            )
            return
        if parts.path == "/jwks":
            self._json(double._jwks)
            return
        if parts.path == "/userinfo":
            token = _bearer(self.headers.get("Authorization", ""))
            claims = double.userinfo(token)
            if claims is None:
                self._json({"error": "invalid_token"}, status=401)
                return
            self._json(claims)
            return
        if parts.path == "/authorize":
            self._authorize(double, parse_qs(parts.query))
            return
        self._json({"error": "not_found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        double: OidcDouble = self.server.double  # type: ignore[attr-defined]
        if urlsplit(self.path).path != "/token":
            self._json({"error": "not_found"}, status=404)
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length).decode("utf-8")
        form = {key: values[-1] for key, values in parse_qs(raw).items()}
        client_id, client_secret = _client_credentials(self.headers.get("Authorization", ""), form)
        issued = double.redeem(
            code=form.get("code", ""),
            verifier=form.get("code_verifier", ""),
            redirect_uri=form.get("redirect_uri", ""),
            client_id=client_id,
            client_secret=client_secret,
        )
        if issued is None:
            self._json({"error": "invalid_grant"}, status=400)
            return
        self._json(issued)

    def _authorize(self, double: OidcDouble, query: dict[str, list[str]]) -> None:
        def one(name: str) -> str:
            values = query.get(name) or [""]
            return values[-1]

        if (
            one("response_type") != "code"
            or one("client_id") != double.client_id
            or one("redirect_uri") != double.redirect_uri
            or one("code_challenge_method") != "S256"
            or not one("code_challenge")
            or not one("state")
        ):
            self._json({"error": "invalid_request"}, status=400)
            return
        code = secrets.token_urlsafe(18)
        double.issue(code, one("code_challenge"), one("redirect_uri"), one("nonce"))
        split = urlsplit(double.redirect_uri)
        location = urlunsplit(
            (
                split.scheme,
                split.netloc,
                split.path,
                urlencode({"code": code, "state": one("state")}),
                "",
            )
        )
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def _bearer(header: str) -> str:
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer":
        return value.strip()
    return ""


def _client_credentials(header: str, form: dict[str, str]) -> tuple[str, str]:
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "basic" and value:
        import base64

        try:
            decoded = base64.b64decode(value).decode("utf-8")
        except ValueError:
            decoded = ""
        client_id, _, client_secret = decoded.partition(":")
        if client_id:
            return client_id, client_secret
    return form.get("client_id", ""), form.get("client_secret", "")
