"""Local password login and the in-process 用户信息同步 double.

The OIDC cases always run. They do not read KILN_SKIP_EXTERNAL_TESTS.
"""

import logging
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import text

from kiln.config import LOCAL_API_TOKEN
from kiln.domain.rbac import PERMISSIONS, permissions_for
from kiln.main import create_app
from tests.oidc_double import OidcDouble
from tests.support import app_settings, auth_headers, truncate_domain

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
    assert LOCAL_API_TOKEN not in machine.text
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
