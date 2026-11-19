"""Local password login and the in-process 用户信息同步 double.

The OIDC cases always run. They do not read KILN_SKIP_EXTERNAL_TESTS.
"""

import logging
import threading
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import text

from kiln.domain.rbac import PERMISSIONS, permissions_for
from kiln.login_limit import LoginLimiter, normalize_username, peer_host
from kiln.main import create_app
from tests.oidc_double import OidcDouble
from tests.support import PROCESS_API_TOKEN, app_settings, auth_headers, truncate_domain

LOCAL_PASSWORD = "correct-horse-1"


def test_local_login_rejects_wrong_and_disabled_passwords(client: TestClient, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    created = client.post(
        "/api/v1/accounts",
        json={
            "username": "ada_login",
            "display_name": "艾达",
            "password": LOCAL_PASSWORD,
            "role_ids": ["builder"],
        },
    )
    assert created.status_code == 201
    assert "password" not in created.text
    assert LOCAL_PASSWORD not in created.text
    engine = client.app.state.engine
    with engine.connect() as connection:
        stored_hash = connection.execute(
            text("SELECT password_hash FROM accounts WHERE username = 'ada_login'")
        ).scalar_one()
    assert stored_hash.startswith("$argon2")
    assert stored_hash not in created.text
    assert stored_hash not in caplog.text
    assert LOCAL_PASSWORD not in caplog.text

    anonymous = TestClient(client.app)
    wrong = anonymous.post(
        "/api/v1/auth/login",
        json={"username": "ada_login", "password": "not-the-password"},
    )
    assert wrong.status_code == 401
    assert wrong.json()["detail"] == "用户名或口令不正确"
    assert "password" not in wrong.text
    assert "not-the-password" not in wrong.text
    assert _session_count(engine) == 0

    with engine.begin() as connection:
        connection.execute(
            text("UPDATE accounts SET status = 'disabled' WHERE username = 'ada_login'")
        )
    disabled = anonymous.post(
        "/api/v1/auth/login",
        json={"username": "ada_login", "password": LOCAL_PASSWORD},
    )
    assert disabled.status_code == 401
    assert disabled.json()["detail"] == "账号已停用"
    assert LOCAL_PASSWORD not in disabled.text
    assert stored_hash not in disabled.text
    assert _session_count(engine) == 0

    with engine.begin() as connection:
        connection.execute(
            text("UPDATE accounts SET status = 'active' WHERE username = 'ada_login'")
        )
    logged_in = anonymous.post(
        "/api/v1/auth/login",
        json={"username": "ada_login", "password": LOCAL_PASSWORD},
    )
    assert logged_in.status_code == 200
    body = logged_in.json()
    assert body["account"]["username"] == "ada_login"
    assert body["account"]["role_ids"] == ["builder"]
    assert body["account"]["source"] == "local"
    assert "idp_issuer" not in body["account"]
    assert "idp_subject" not in body["account"]
    assert "password" not in logged_in.text
    assert LOCAL_PASSWORD not in logged_in.text
    assert stored_hash not in logged_in.text
    cookie = logged_in.headers["set-cookie"]
    parts = [item.strip().lower() for item in cookie.split(";")]
    assert "httponly" in parts
    assert "samesite=lax" in parts
    assert "secure" not in parts
    token = body["token"]
    assert anonymous.get("/api/v1/accounts").status_code == 200
    bearer = TestClient(client.app)
    listed = bearer.get("/api/v1/accounts", headers={"Authorization": f"Bearer {token}"})
    assert listed.status_code == 200
    assert listed.json()["items"][0]["username"] == "ada_login"
    assert listed.json()["items"][0]["source"] == "local"
    assert listed.json()["items"][0] == body["account"]


def test_session_survives_restart_and_logout_revokes(tmp_path: Path) -> None:
    settings = app_settings(kiln_artifact_root=str(tmp_path / "artifacts"))
    first = create_app(settings)
    with TestClient(first) as client:
        truncate_domain(first.state.engine)
        assert (
            client.post(
                "/api/v1/accounts",
                headers=auth_headers(),
                json={
                    "username": "ada_session",
                    "display_name": "艾达",
                    "password": LOCAL_PASSWORD,
                },
            ).status_code
            == 201
        )
        logged_in = client.post(
            "/api/v1/auth/login",
            json={"username": "ada_session", "password": LOCAL_PASSWORD},
        )
        assert logged_in.status_code == 200
        token = logged_in.json()["token"]
    second = create_app(settings)
    with TestClient(second) as client:
        assert client.get("/api/v1/accounts").status_code == 401
        kept = client.get("/api/v1/accounts", headers={"Authorization": f"Bearer {token}"})
        assert kept.status_code == 200
        assert kept.json()["items"][0]["username"] == "ada_session"
        logged_out = client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert logged_out.status_code == 204
        assert (
            client.get(
                "/api/v1/accounts",
                headers={"Authorization": f"Bearer {token}"},
            ).status_code
            == 401
        )
        with second.state.engine.connect() as connection:
            revoked = connection.execute(
                text("SELECT count(*) FROM sessions WHERE revoked_at IS NOT NULL")
            ).scalar_one()
        assert revoked == 1


def test_reader_is_forbidden_and_machine_token_still_writes(client: TestClient) -> None:
    created = client.post(
        "/api/v1/accounts",
        json={
            "username": "ada_reader",
            "display_name": "只读",
            "password": LOCAL_PASSWORD,
            "role_ids": ["reader"],
        },
    )
    assert created.status_code == 201
    reader = TestClient(client.app)
    logged_in = reader.post(
        "/api/v1/auth/login",
        json={"username": "ada_reader", "password": LOCAL_PASSWORD},
    )
    assert logged_in.status_code == 200
    token = logged_in.json()["token"]
    denied = reader.post(
        "/api/v1/artifacts",
        json={"name": "reader-lib", "package_type": "generic"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert denied.status_code == 403
    # The session cookie is checked before the machine bearer.
    mixed = reader.post(
        "/api/v1/accounts",
        json={"username": "should_fail", "display_name": "不行"},
        headers=auth_headers(),
    )
    assert mixed.status_code == 403
    machine = TestClient(client.app)
    allowed = machine.post(
        "/api/v1/artifacts",
        json={"name": "machine-lib", "package_type": "generic"},
        headers=auth_headers(),
    )
    assert allowed.status_code == 201
    published = client.post(
        "/api/v1/skills",
        json={"name": "读者技能", "version": "1.0.0"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert published.status_code == 403


def test_oidc_routes_are_closed_until_configured(client: TestClient) -> None:
    sync = client.get("/api/v1/auth/sync")
    assert sync.status_code == 200
    assert sync.json() == {"configured": False}
    start = client.get("/api/v1/auth/oidc/start")
    assert start.status_code == 503
    assert start.json()["detail"] == "用户信息同步未配置"
    callback = client.get("/api/v1/auth/oidc/callback")
    assert callback.status_code == 503
    created = client.post(
        "/api/v1/accounts",
        json={
            "username": "local_only",
            "display_name": "本地",
            "password": LOCAL_PASSWORD,
        },
    )
    assert created.status_code == 201
    logged_in = TestClient(client.app).post(
        "/api/v1/auth/login",
        json={"username": "local_only", "password": LOCAL_PASSWORD},
    )
    assert logged_in.status_code == 200
    assert logged_in.json()["account"]["role_ids"] == ["reader"]


def test_oidc_sync_creates_and_signs_in_without_copying_roles(tmp_path: Path) -> None:
    with OidcDouble() as double:
        app = create_app(_oidc_settings(tmp_path, double))
        with TestClient(app) as client:
            truncate_domain(app.state.engine)
            created = _complete_oidc(client, double)
            assert created.status_code == 200, created.text
            body = created.json()
            assert body["account"]["role_ids"] == ["reader"]
            assert body["account"]["source"] == "sync"
            assert "idp_issuer" not in body["account"]
            assert "idp_subject" not in body["account"]
            assert "password" not in created.text
            assert double.saw_pkce is True
            assert double.last_id_claims["groups"] == ["company-admin"]
            assert double.last_id_claims["roles"] == ["publisher"]
            account_id = body["account"]["id"]
            me = client.get("/api/v1/auth/me")
            assert me.status_code == 200
            assert me.json()["account"]["source"] == "sync"
            assert me.json()["account"] == body["account"]
            assert "idp_issuer" not in me.json()["account"]
            assert "idp_subject" not in me.json()["account"]
            listed = client.get("/api/v1/accounts", headers=auth_headers())
            assert listed.status_code == 200
            synced = next(item for item in listed.json()["items"] if item["id"] == account_id)
            assert synced["source"] == "sync"
            assert synced == body["account"]
            with app.state.engine.connect() as connection:
                row = connection.execute(
                    text(
                        "SELECT password_hash, idp_issuer, idp_subject, employment_status, status "
                        "FROM accounts WHERE id = :id"
                    ),
                    {"id": account_id},
                ).one()
            assert row.password_hash is None
            assert row.idp_issuer == double.issuer
            assert row.idp_subject == "person-1"
            assert row.employment_status == "active"
            assert row.status == "active"
            again = _complete_oidc(client, double)
            assert again.status_code == 200
            assert again.json()["account"]["id"] == account_id
            assert again.json()["account"]["role_ids"] == ["reader"]
            assert _account_count(app.state.engine) == 1
            browser = _complete_oidc(client, double, accept="text/html")
            assert browser.status_code == 303
            assert browser.headers["location"] == "/"


def test_oidc_rejects_missing_or_inactive_employment(tmp_path: Path) -> None:
    with OidcDouble() as double:
        app = create_app(_oidc_settings(tmp_path, double))
        with TestClient(app) as client:
            truncate_domain(app.state.engine)
            double.identity.pop("employment")
            missing = _complete_oidc(client, double)
            assert missing.status_code == 401
            assert _session_count(app.state.engine) == 0
            assert _account_count(app.state.engine) == 0
            double.identity["employment"] = "left"
            inactive = _complete_oidc(client, double)
            assert inactive.status_code == 401
            assert inactive.json()["detail"] == "在职状态未通过"
            assert _session_count(app.state.engine) == 0
            assert _account_count(app.state.engine) == 0


def test_oidc_does_not_take_over_a_local_account_with_the_same_email(tmp_path: Path) -> None:
    with OidcDouble() as double:
        app = create_app(_oidc_settings(tmp_path, double))
        with TestClient(app) as client:
            truncate_domain(app.state.engine)
            local = client.post(
                "/api/v1/accounts",
                headers=auth_headers(),
                json={
                    "username": "local_ada",
                    "display_name": "本地",
                    "email": "person@example.com",
                    "role_ids": ["builder"],
                    "password": LOCAL_PASSWORD,
                },
            )
            assert local.status_code == 201
            assert local.json()["source"] == "local"
            double.identity["email"] = "person@example.com"
            double.identity["preferred_username"] = "local_ada"
            synced = _complete_oidc(client, double)
            assert synced.status_code == 200
            assert synced.json()["account"]["role_ids"] == ["reader"]
            assert synced.json()["account"]["source"] == "sync"
            assert synced.json()["account"]["id"] != local.json()["id"]
            with app.state.engine.connect() as connection:
                rows = connection.execute(
                    text("SELECT username, idp_subject FROM accounts ORDER BY username")
                ).all()
            assert [row.username for row in rows] == ["local_ada", "local_ada_1"]
            local_row = next(row for row in rows if row.username == "local_ada")
            assert local_row.idp_subject is None


def test_disabled_synced_account_gets_no_session(tmp_path: Path) -> None:
    with OidcDouble() as double:
        app = create_app(_oidc_settings(tmp_path, double))
        with TestClient(app) as client:
            truncate_domain(app.state.engine)
            first = _complete_oidc(client, double)
            assert first.status_code == 200
            token = first.json()["token"]
            with app.state.engine.begin() as connection:
                connection.execute(text("UPDATE accounts SET status = 'disabled'"))
            denied = client.get(
                "/api/v1/accounts",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert denied.status_code == 401
            before = _session_count(app.state.engine)
            second = _complete_oidc(client, double)
            assert second.status_code == 401
            assert second.json()["detail"] == "账号已停用"
            assert _session_count(app.state.engine) == before


def test_auth_me_returns_the_principal_and_hides_secrets(client: TestClient) -> None:
    anonymous = TestClient(client.app)
    denied = anonymous.get("/api/v1/auth/me")
    assert denied.status_code == 401
    assert denied.json()["detail"] == "需要访问令牌"
    assert denied.headers["www-authenticate"] == "Bearer"
    assert denied.headers["cache-control"] == "no-store"
    assert "password" not in denied.text
    assert "token" not in denied.text

    spec = client.get("/openapi.json").json()
    me_schema = spec["components"]["schemas"]["SessionPrincipal"]
    assert set(me_schema["properties"]) == {"kind", "account", "permissions"}
    assert spec["paths"]["/api/v1/auth/me"]["get"].get("security")
    for path, method in (
        ("/api/v1/auth/login", "post"),
        ("/api/v1/auth/logout", "post"),
        ("/api/v1/auth/sync", "get"),
        ("/api/v1/auth/oidc/start", "get"),
        ("/api/v1/auth/oidc/callback", "get"),
    ):
        assert "security" not in spec["paths"][path][method], path

    catalog = [item.code for item in PERMISSIONS]
    listed = [item["code"] for item in client.get("/api/v1/rbac/permissions").json()["items"]]
    assert listed == catalog

    cookie_client, token, account = _login_user(client, "ada_me", ["builder"])
    by_cookie = cookie_client.get("/api/v1/auth/me")
    assert by_cookie.status_code == 200
    assert by_cookie.headers["cache-control"] == "no-store"
    body = by_cookie.json()
    expected = [code for code in catalog if code in permissions_for(account["role_ids"])]
    assert body["kind"] == "user"
    assert account["source"] == "local"
    assert "idp_issuer" not in account
    assert "idp_subject" not in account
    assert body["account"] == account
    assert body["permissions"] == expected
    assert set(body["permissions"]) == permissions_for(account["role_ids"])
    assert "skill.submit" in body["permissions"]
    assert "agent.draft_skill" in body["permissions"]
    assert "skill.publish" not in body["permissions"]
    _assert_me_hides_secrets(by_cookie, token, client.app.state.engine, account["id"])

    bearer = TestClient(client.app)
    by_bearer = bearer.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert by_bearer.status_code == 200
    assert by_bearer.headers["cache-control"] == "no-store"
    assert by_bearer.json() == body
    _assert_me_hides_secrets(by_bearer, token, client.app.state.engine, account["id"])

    machine = client.get("/api/v1/auth/me")
    assert machine.status_code == 200
    assert machine.headers["cache-control"] == "no-store"
    assert machine.json() == {"kind": "machine", "account": None, "permissions": catalog}
    assert PROCESS_API_TOKEN not in machine.text
    assert "password" not in machine.text
    assert "expires_at" not in machine.text
    assert "token_hash" not in machine.text

    logged_out = bearer.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert logged_out.status_code == 204
    revoked = bearer.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert revoked.status_code == 401
    assert revoked.headers["cache-control"] == "no-store"
    assert token not in revoked.text
    assert cookie_client.get("/api/v1/auth/me").status_code == 401

    disabled_client, disabled_token, disabled_account = _login_user(
        client, "ada_me_off", ["reader"]
    )
    engine = client.app.state.engine
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE accounts SET status = 'disabled' WHERE id::text = :id"),
            {"id": disabled_account["id"]},
        )
    disabled = disabled_client.get("/api/v1/auth/me")
    assert disabled.status_code == 401
    assert disabled.headers["cache-control"] == "no-store"
    assert disabled_token not in disabled.text
    assert LOCAL_PASSWORD not in disabled.text

    expired_client, expired_token, expired_account = _login_user(client, "ada_me_old", ["reader"])
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE sessions SET created_at = now() - interval '2 hours', "
                "expires_at = now() - interval '1 hour' WHERE account_id::text = :id"
            ),
            {"id": expired_account["id"]},
        )
    expired = expired_client.get("/api/v1/auth/me")
    assert expired.status_code == 401
    assert expired.headers["cache-control"] == "no-store"
    assert expired_token not in expired.text


def _login_user(
    client: TestClient,
    username: str,
    role_ids: list[str],
) -> tuple[TestClient, str, dict]:
    created = client.post(
        "/api/v1/accounts",
        json={
            "username": username,
            "display_name": "当前用户",
            "password": LOCAL_PASSWORD,
            "role_ids": role_ids,
        },
    )
    assert created.status_code == 201, created.text
    assert "password" not in created.text
    session = TestClient(client.app)
    logged_in = session.post(
        "/api/v1/auth/login",
        json={"username": username, "password": LOCAL_PASSWORD},
    )
    assert logged_in.status_code == 200, logged_in.text
    body = logged_in.json()
    return session, body["token"], body["account"]


def _assert_me_hides_secrets(response, token: str, engine, account_id: str) -> None:
    assert set(response.json()) == {"kind", "account", "permissions"}
    assert "password" not in response.text
    assert "expires_at" not in response.text
    assert "token_hash" not in response.text
    assert "password_hash" not in response.text
    assert token not in response.text
    assert LOCAL_PASSWORD not in response.text
    with engine.connect() as connection:
        stored = connection.execute(
            text(
                "SELECT sessions.token_hash, accounts.password_hash "
                "FROM sessions JOIN accounts ON accounts.id = sessions.account_id "
                "WHERE accounts.id::text = :id"
            ),
            {"id": account_id},
        ).one()
    assert stored.token_hash not in response.text
    assert stored.password_hash not in response.text


def _oidc_settings(tmp_path: Path, double: OidcDouble):
    return app_settings(
        kiln_artifact_root=str(tmp_path / "artifacts"),
        kiln_oidc_issuer_url=double.issuer,
        kiln_oidc_client_id=double.client_id,
        kiln_oidc_client_secret=double.client_secret,
        kiln_oidc_redirect_uri=double.redirect_uri,
        kiln_oidc_employment_claim=double.employment_claim,
        kiln_oidc_employment_active_values="active",
    )


def _complete_oidc(client: TestClient, double: OidcDouble, *, accept: str | None = None):
    start = client.get("/api/v1/auth/oidc/start", follow_redirects=False)
    assert start.status_code == 302, start.text
    location = start.headers["location"]
    assert location.startswith(f"{double.issuer}/authorize?")
    assert "code_challenge=" in location
    assert "code_challenge_method=S256" in location
    with httpx.Client(trust_env=False, follow_redirects=False) as http:
        authorized = http.get(location)
    assert authorized.status_code == 302, authorized.text
    parsed = urlsplit(authorized.headers["location"])
    headers = {"Accept": accept} if accept else {}
    return client.get(
        f"{parsed.path}?{parsed.query}",
        headers=headers,
        follow_redirects=False,
    )


def _session_count(engine) -> int:
    with engine.connect() as connection:
        return connection.execute(text("SELECT count(*) FROM sessions")).scalar_one()


def _account_count(engine) -> int:
    with engine.connect() as connection:
        return connection.execute(text("SELECT count(*) FROM accounts")).scalar_one()


_LIMIT_DETAIL = "尝试次数过多，请稍后再试"
_SHARED_IP = "经控制台登录时，多位用户共享控制台的对端地址，IP 键是同一条。"
_RESTART_CLEARS = "重启 API 进程会清空进程内计数。"


class _Clock:
    def __init__(self, now: float = 10_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_login_limiter_drops_expired_failures_and_keeps_the_ip_key() -> None:
    """The window is time-based. Success clears only the username key."""

    clock = _Clock(0.0)
    limiter = LoginLimiter(
        window_seconds=60,
        username_max_failures=2,
        ip_max_failures=2,
        clock=clock,
    )
    assert isinstance(limiter.lock, type(threading.Lock()))
    assert normalize_username("  Ada ") == "ada"
    assert normalize_username("   ") is None
    assert peer_host(None) is None
    assert peer_host(SimpleNamespace(host="")) is None
    assert peer_host(SimpleNamespace(host="203.0.113.8")) == "203.0.113.8"

    assert limiter.record_failure("ada", "203.0.113.1") is None
    clock.now = 10.0
    # Both keys reach 2. Retry-After follows the older stamp on each key.
    assert limiter.record_failure("ada", "203.0.113.1") == 50
    assert limiter.blocked("ada", "203.0.113.9") == 50
    assert limiter.blocked("other", "203.0.113.1") == 50
    limiter.clear_username("ada")
    assert limiter.blocked("ada", None) is None
    assert limiter.blocked(None, "203.0.113.1") == 50
    clock.now = 70.0
    assert limiter.blocked("ada", "203.0.113.1") is None
    assert limiter.record_failure(None, None) is None


def test_username_limit_returns_429_and_stops_password_checks(tmp_path: Path) -> None:
    app = _limited_app(tmp_path, username_max=3, ip_max=1000)
    with TestClient(app) as http:
        truncate_domain(app.state.engine)
        _create_account(http, "ada_limit")
        anon = TestClient(app)
        store = app.state.store
        calls = {"n": 0}
        original = store.authenticate_password

        def spy(username: str, password: str):
            calls["n"] += 1
            return original(username, password)

        store.authenticate_password = spy  # type: ignore[method-assign]
        first = _post_login(anon, "ada_limit", "not-the-password")
        second = _post_login(anon, "ada_limit", "not-the-password")
        assert first.status_code == 401
        assert first.json()["detail"] == "用户名或口令不正确"
        assert second.status_code == 401
        assert calls["n"] == 2
        capped = _post_login(anon, "ada_limit", "not-the-password")
        _assert_limited(capped)
        assert calls["n"] == 3
        assert _session_count(app.state.engine) == 0
        before = calls["n"]
        blocked = _post_login(anon, "ada_limit", LOCAL_PASSWORD)
        _assert_limited(blocked)
        assert calls["n"] == before
        assert _session_count(app.state.engine) == 0
        assert "set-cookie" not in blocked.headers


def test_disabled_login_stays_401_until_the_window_is_full(tmp_path: Path) -> None:
    app = _limited_app(tmp_path, username_max=3, ip_max=1000)
    with TestClient(app) as http:
        truncate_domain(app.state.engine)
        _create_account(http, "ada_off")
        with app.state.engine.begin() as connection:
            connection.execute(
                text("UPDATE accounts SET status = 'disabled' WHERE username = 'ada_off'")
            )
        anon = TestClient(app)
        disabled = _post_login(anon, "ada_off", LOCAL_PASSWORD)
        assert disabled.status_code == 401
        assert disabled.json()["detail"] == "账号已停用"
        wrong = _post_login(anon, "ada_off", "not-the-password")
        assert wrong.status_code == 401
        assert wrong.json()["detail"] == "用户名或口令不正确"
        capped = _post_login(anon, "ada_off", LOCAL_PASSWORD)
        _assert_limited(capped)
        store = app.state.store
        calls = {"n": 0}
        original = store.authenticate_password

        def spy(username: str, password: str):
            calls["n"] += 1
            return original(username, password)

        store.authenticate_password = spy  # type: ignore[method-assign]
        blocked = _post_login(anon, "ada_off", LOCAL_PASSWORD)
        _assert_limited(blocked)
        assert calls["n"] == 0
        assert _session_count(app.state.engine) == 0
        status = connection_status(app.state.engine, "ada_off")
        assert status == "disabled"


def test_successful_login_clears_that_username_only(tmp_path: Path) -> None:
    app = _limited_app(tmp_path, username_max=3, ip_max=1000)
    with TestClient(app) as http:
        truncate_domain(app.state.engine)
        _create_account(http, "ada_clear")
        _create_account(http, "bea_clear")
        anon = TestClient(app)
        assert _post_login(anon, "ada_clear", "not-the-password").status_code == 401
        assert _post_login(anon, "ada_clear", "not-the-password").status_code == 401
        # A different username still has its own counter while ada is one failure from the cap.
        assert _post_login(anon, "bea_clear", "not-the-password").status_code == 401
        logged_in = _post_login(anon, "ada_clear", LOCAL_PASSWORD)
        assert logged_in.status_code == 200
        assert logged_in.json()["account"]["username"] == "ada_clear"
        assert "set-cookie" in logged_in.headers
        assert _session_count(app.state.engine) == 1
        # The success cleared ada, so the next two failures are still 401.
        assert _post_login(anon, "ada_clear", "not-the-password").status_code == 401
        assert _post_login(anon, "ada_clear", "not-the-password").status_code == 401
        _assert_limited(_post_login(anon, "ada_clear", "not-the-password"))
        # bea used one failure above and is not sharing ada's cap.
        assert _post_login(anon, "bea_clear", "not-the-password").status_code == 401


def test_login_names_share_one_key_across_case_and_space(tmp_path: Path) -> None:
    app = _limited_app(tmp_path, username_max=3, ip_max=1000)
    with TestClient(app):
        truncate_domain(app.state.engine)
        anon = TestClient(app)
        assert _post_login(anon, "Ada", "not-the-password").status_code == 401
        assert _post_login(anon, "ada", "not-the-password").status_code == 401
        _assert_limited(_post_login(anon, "  ADA  ", "not-the-password"))
        store = app.state.store
        calls = {"n": 0}

        def spy(username: str, password: str):
            calls["n"] += 1
            return None, "invalid"

        store.authenticate_password = spy  # type: ignore[method-assign]
        _assert_limited(_post_login(anon, "\tada\t", "not-the-password"))
        assert calls["n"] == 0


def test_blank_username_skips_the_username_key_and_still_honors_ip(tmp_path: Path) -> None:
    app = _limited_app(tmp_path, username_max=3, ip_max=1000)
    with TestClient(app):
        truncate_domain(app.state.engine)
        anon = TestClient(app)
        for name in (" ", "  ", "   ", "\t"):
            rejected = _post_login(anon, name, "not-the-password")
            assert rejected.status_code == 401
            assert rejected.json()["detail"] == "用户名或口令不正确"
        assert _post_login(anon, "ada_blank", "not-the-password").status_code == 401

    ip_app = _limited_app(tmp_path / "ip", username_max=100, ip_max=5)
    with TestClient(ip_app):
        truncate_domain(ip_app.state.engine)
        anon = TestClient(ip_app, client=("203.0.113.20", 50000))
        for _ in range(4):
            assert _post_login(anon, " ", "not-the-password").status_code == 401
        _assert_limited(_post_login(anon, "   ", "not-the-password"))


def test_ip_limit_is_independent_of_the_username_budget(tmp_path: Path) -> None:
    clock = _Clock(5_000.0)
    app = _limited_app(tmp_path, username_max=100, ip_max=5, clock=clock)
    with TestClient(app) as http:
        truncate_domain(app.state.engine)
        _create_account(http, "ada_ip")
        left = TestClient(app, client=("203.0.113.10", 50000))
        right = TestClient(app, client=("198.51.100.10", 50000))
        for index in range(4):
            failed = _post_login(left, f"user_{index}", "not-the-password")
            assert failed.status_code == 401
        capped = _post_login(
            left,
            "user_4",
            "not-the-password",
            extra_headers={"X-Forwarded-For": "198.51.100.10", "X-Real-IP": "198.51.100.10"},
        )
        _assert_limited(capped, retry_after="60")
        store = app.state.store
        calls = {"n": 0}
        original = store.authenticate_password

        def spy(username: str, password: str):
            calls["n"] += 1
            return original(username, password)

        store.authenticate_password = spy  # type: ignore[method-assign]
        _assert_limited(
            _post_login(
                left,
                "user_5",
                "not-the-password",
                extra_headers={"X-Forwarded-For": "203.0.113.99"},
            )
        )
        assert calls["n"] == 0
        # A different peer is not limited, even if it copies the blocked address into a header.
        other = _post_login(
            right,
            "user_6",
            "not-the-password",
            extra_headers={"X-Forwarded-For": "203.0.113.10", "X-Real-IP": "203.0.113.10"},
        )
        assert other.status_code == 401
        assert _post_login(right, "ada_ip", LOCAL_PASSWORD).status_code == 200


def test_success_does_not_clear_the_ip_key(tmp_path: Path) -> None:
    app = _limited_app(tmp_path, username_max=100, ip_max=5)
    with TestClient(app) as http:
        truncate_domain(app.state.engine)
        _create_account(http, "ada_keep_ip")
        anon = TestClient(app, client=("203.0.113.40", 50000))
        for index in range(4):
            assert _post_login(anon, f"miss_{index}", "not-the-password").status_code == 401
        assert _post_login(anon, "ada_keep_ip", LOCAL_PASSWORD).status_code == 200
        _assert_limited(_post_login(anon, "miss_after", "not-the-password"))


def test_login_limit_does_not_change_status_or_existing_sessions(tmp_path: Path) -> None:
    app = _limited_app(tmp_path, username_max=3, ip_max=1000)
    with TestClient(app) as http:
        truncate_domain(app.state.engine)
        _create_account(http, "ada_keep")
        session = TestClient(app)
        logged_in = _post_login(session, "ada_keep", LOCAL_PASSWORD)
        assert logged_in.status_code == 200
        token = logged_in.json()["token"]
        anon = TestClient(app)
        assert _post_login(anon, "ada_keep", "not-the-password").status_code == 401
        assert _post_login(anon, "ada_keep", "not-the-password").status_code == 401
        _assert_limited(_post_login(anon, "ada_keep", "not-the-password"))
        assert connection_status(app.state.engine, "ada_keep") == "active"
        me = session.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["account"]["username"] == "ada_keep"
        bearer = TestClient(app)
        by_token = bearer.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert by_token.status_code == 200
        assert _session_count(app.state.engine) == 1
        _assert_limited(_post_login(anon, "ada_keep", LOCAL_PASSWORD))
        assert _session_count(app.state.engine) == 1


def test_other_auth_entries_do_not_gain_429(tmp_path: Path) -> None:
    app = _limited_app(tmp_path, username_max=3, ip_max=1000)
    with TestClient(app) as http:
        truncate_domain(app.state.engine)
        created = _create_account(http, "ada_other")
        session = TestClient(app)
        assert _post_login(session, "ada_other", LOCAL_PASSWORD).status_code == 200
        anon = TestClient(app)
        assert _post_login(anon, "ada_other", "not-the-password").status_code == 401
        assert _post_login(anon, "ada_other", "not-the-password").status_code == 401
        _assert_limited(_post_login(anon, "ada_other", "not-the-password"))
        start = http.get("/api/v1/auth/oidc/start")
        assert start.status_code == 503
        assert start.json()["detail"] == "用户信息同步未配置"
        callback = http.get("/api/v1/auth/oidc/callback")
        assert callback.status_code == 503
        assert callback.json()["detail"] == "用户信息同步未配置"
        bad_token = http.get(
            "/api/v1/accounts",
            headers={"Authorization": "Bearer not-a-machine-token"},
        )
        assert bad_token.status_code == 401
        assert bad_token.json()["detail"] == "需要访问令牌"
        changed = session.post(
            "/api/v1/auth/password",
            json={"current_password": "wrong-pass", "new_password": "another-ok-1"},
        )
        assert changed.status_code == 400
        assert changed.json()["detail"] == "当前口令不正确"
        reset = http.post(
            f"/api/v1/accounts/{created['id']}/password",
            headers=auth_headers(),
            json={"password": "reset-pass-1"},
        )
        # Resetting ada revokes her sessions. The call itself must not be 429.
        assert reset.status_code == 204


def test_login_window_expires_to_401_then_200(tmp_path: Path) -> None:
    """Old failures leave the window without a success or a new process."""

    clock = _Clock(1_000.0)
    app = _limited_app(tmp_path, username_max=3, ip_max=1000, window=60, clock=clock)
    with TestClient(app) as http:
        truncate_domain(app.state.engine)
        _create_account(http, "ada_window")
        anon = TestClient(app)
        assert _post_login(anon, "ada_window", "not-the-password").status_code == 401
        clock.now = 1_010.0
        assert _post_login(anon, "ada_window", "not-the-password").status_code == 401
        clock.now = 1_020.0
        capped = _post_login(anon, "ada_window", "not-the-password")
        _assert_limited(capped, retry_after="40")
        clock.now = 1_059.8
        _assert_limited(_post_login(anon, "ada_window", LOCAL_PASSWORD), retry_after="1")
        # 1000 and 1010 have left the window. One stamp remains, so this failure is 401.
        clock.now = 1_079.0
        again = _post_login(anon, "ada_window", "not-the-password")
        assert again.status_code == 401
        assert again.json()["detail"] == "用户名或口令不正确"
        clock.now = 1_139.0
        ok = _post_login(anon, "ada_window", LOCAL_PASSWORD)
        assert ok.status_code == 200
        assert ok.json()["account"]["username"] == "ada_window"
        assert _session_count(app.state.engine) == 1


def test_login_counters_are_not_shared_across_app_instances(tmp_path: Path) -> None:
    settings = app_settings(
        kiln_artifact_root=str(tmp_path / "artifacts"),
        kiln_login_window_seconds=60,
        kiln_login_username_max_failures=3,
        kiln_login_ip_max_failures=1000,
    )
    first = create_app(settings)
    second = create_app(settings)
    with TestClient(first) as left, TestClient(second) as right:
        truncate_domain(first.state.engine)
        assert first.state.login_limit is not second.state.login_limit
        for _ in range(2):
            assert _post_login(left, "ada_proc", "not-the-password").status_code == 401
        _assert_limited(_post_login(left, "ada_proc", "not-the-password"))
        assert _post_login(right, "ada_proc", "not-the-password").status_code == 401


def test_login_limit_docs_and_env_keys() -> None:
    root = Path(__file__).resolve().parents[2]
    conftest = (root / "backend" / "tests" / "conftest.py").read_text(encoding="utf-8")
    keys = conftest.split("ENV_KEYS = (", 1)[1].split(")", 1)[0]
    for name in (
        "KILN_LOGIN_WINDOW_SECONDS",
        "KILN_LOGIN_USERNAME_MAX_FAILURES",
        "KILN_LOGIN_IP_MAX_FAILURES",
    ):
        assert name in keys
    example = (root / ".env.example").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")
    deploy = (root / "docs" / "DEPLOY.md").read_text(encoding="utf-8")
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    for document in (example, readme, deploy):
        assert "KILN_LOGIN_WINDOW_SECONDS" in document
        assert "KILN_LOGIN_USERNAME_MAX_FAILURES" in document
        assert "KILN_LOGIN_IP_MAX_FAILURES" in document
        assert "900" in document
        assert "尝试次数过多，请稍后再试" in document
        assert "Retry-After" in document
        assert _SHARED_IP in document
        assert _RESTART_CLEARS in document
        assert "联邦" not in document
        assert "federation" not in document.casefold()
    assert "KILN_LOGIN_WINDOW_SECONDS: ${KILN_LOGIN_WINDOW_SECONDS:-900}" in compose
    assert "KILN_LOGIN_USERNAME_MAX_FAILURES: ${KILN_LOGIN_USERNAME_MAX_FAILURES:-5}" in compose
    assert "KILN_LOGIN_IP_MAX_FAILURES: ${KILN_LOGIN_IP_MAX_FAILURES:-30}" in compose
    source = (root / "backend" / "kiln" / "login_limit.py").read_text(encoding="utf-8")
    assert "self.lock = threading.Lock()" in source
    assert "asyncio.Lock()" not in source
    assert "import asyncio" not in source


def _limited_app(
    tmp_path: Path,
    *,
    username_max: int,
    ip_max: int,
    window: int = 60,
    clock: _Clock | None = None,
):
    app = create_app(
        app_settings(
            kiln_artifact_root=str(tmp_path / "artifacts"),
            kiln_login_window_seconds=window,
            kiln_login_username_max_failures=username_max,
            kiln_login_ip_max_failures=ip_max,
        )
    )
    if clock is not None:
        app.state.login_limit.clock = clock
    return app


def _create_account(client: TestClient, username: str) -> dict:
    created = client.post(
        "/api/v1/accounts",
        headers=auth_headers(),
        json={
            "username": username,
            "display_name": "限速",
            "password": LOCAL_PASSWORD,
            "role_ids": ["reader"],
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


def _post_login(
    client: TestClient,
    username: str,
    password: str,
    extra_headers: dict[str, str] | None = None,
):
    return client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers=extra_headers,
    )


def _assert_limited(response, retry_after: str | None = None) -> None:
    assert response.status_code == 429, response.text
    assert response.json() == {"detail": _LIMIT_DETAIL}
    assert "www-authenticate" not in response.headers
    assert int(response.headers["retry-after"]) >= 1
    if retry_after is not None:
        assert response.headers["retry-after"] == retry_after
    assert "set-cookie" not in response.headers


def connection_status(engine, username: str) -> str:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT status FROM accounts WHERE username = :name"),
            {"name": username},
        ).scalar_one()
