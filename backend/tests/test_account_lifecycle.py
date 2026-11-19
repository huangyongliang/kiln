"""S2b account lifecycle: status, roles, passwords, locks, and migration 0012."""

import logging
import threading
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.engine import Engine, make_url

from kiln.db.engine import create_db_engine
from kiln.db.migrate import upgrade_database
from kiln.db.models import Base
from kiln.db.store import PostgresStore
from kiln.main import create_app
from tests.oidc_double import OidcDouble
from tests.support import auth_headers, postgres_url, truncate_domain
from tests.test_login import _complete_oidc, _oidc_settings
from tests.test_persistence import _alembic_config

LOCAL_PASSWORD = "correct-horse-1"
NEW_PASSWORD = "replacement-horse-2"
ROOT = Path(__file__).resolve().parents[2]
_BUILD = {
    "repository": "ada/demo",
    "ref": "main",
    "artifact_name": "gated-pkg",
    "version": "1",
    "package_type": "generic",
}
LAST_ADMIN = "不能停用或降级最后一个管理员"
SELF_ADMIN = "不能停用自己或移除自己的管理员角色"
NO_LOCAL_PASSWORD = "该账号没有本地口令"


def test_disable_blocks_password_session_and_sync(client: TestClient, tmp_path: Path) -> None:
    """Acceptance 11. API disable rejects login, existing sessions, and sync login."""

    admin = _login(client, "ada_life_admin", ["admin"])
    target = _login(client, "ada_life_target", ["builder"])
    second = TestClient(client.app)
    again = second.post(
        "/api/v1/auth/login",
        json={"username": "ada_life_target", "password": LOCAL_PASSWORD},
    )
    assert again.status_code == 200, again.text
    second_token = again.json()["token"]
    before = target["account"]["updated_at"]
    disabled = admin["session"].patch(
        f"/api/v1/accounts/{target['account']['id']}",
        json={"status": "disabled"},
    )
    assert disabled.status_code == 200, disabled.text
    body = disabled.json()
    assert body["status"] == "disabled"
    assert body["updated_by"] == admin["account"]["id"]
    assert _parsed(body["updated_at"]) > _parsed(before)
    assert _active_sessions(client.app.state.engine, target["account"]["id"]) == 0
    assert target["session"].get("/api/v1/auth/me").status_code == 401
    bearer = TestClient(client.app)
    denied = bearer.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {second_token}"},
    )
    assert denied.status_code == 401
    logged = TestClient(client.app).post(
        "/api/v1/auth/login",
        json={"username": "ada_life_target", "password": LOCAL_PASSWORD},
    )
    assert logged.status_code == 401
    assert logged.json()["detail"] == "账号已停用"

    with OidcDouble() as double:
        app = create_app(_oidc_settings(tmp_path, double))
        with TestClient(app) as synced:
            truncate_domain(app.state.engine)
            first = _complete_oidc(synced, double)
            assert first.status_code == 200, first.text
            account_id = first.json()["account"]["id"]
            token = first.json()["token"]
            machine = TestClient(app, headers=auth_headers())
            turned_off = machine.patch(
                f"/api/v1/accounts/{account_id}",
                json={"status": "disabled"},
            )
            assert turned_off.status_code == 200, turned_off.text
            assert synced.get("/api/v1/auth/me").status_code == 401
            assert (
                TestClient(app).get(
                    "/api/v1/auth/me",
                    headers={"Authorization": f"Bearer {token}"},
                ).status_code
                == 401
            )
            callback = _complete_oidc(synced, double)
            assert callback.status_code == 401
            assert callback.json()["detail"] == "账号已停用"


def test_reenable_keeps_old_sessions_revoked(client: TestClient) -> None:
    """Acceptance 12. Enabling the account does not revive the revoked session."""

    admin = _login(client, "ada_enable_admin", ["admin"])
    target = _login(client, "ada_enable_target", ["reader"])
    token = target["token"]
    disabled = admin["session"].patch(
        f"/api/v1/accounts/{target['account']['id']}",
        json={"status": "disabled"},
    )
    assert disabled.status_code == 200, disabled.text
    enabled = admin["session"].patch(
        f"/api/v1/accounts/{target['account']['id']}",
        json={"status": "active"},
    )
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["status"] == "active"
    assert target["session"].get("/api/v1/auth/me").status_code == 401
    stale = TestClient(client.app).get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert stale.status_code == 401
    fresh = TestClient(client.app).post(
        "/api/v1/auth/login",
        json={"username": "ada_enable_target", "password": LOCAL_PASSWORD},
    )
    assert fresh.status_code == 200, fresh.text


def test_role_change_applies_on_the_same_session(client: TestClient) -> None:
    """Acceptance 13. The same session loses executor.run and is not revoked."""

    admin = _login(client, "ada_role_admin", ["admin"])
    target = _login(client, "ada_role_builder", ["builder"])
    opened = target["session"].get("/api/v1/auth/me")
    assert opened.status_code == 200, opened.text
    assert "executor.run" in opened.json()["permissions"]
    changed = admin["session"].patch(
        f"/api/v1/accounts/{target['account']['id']}",
        json={"role_ids": ["reader"]},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["role_ids"] == ["reader"]
    denied = target["session"].post("/api/v1/builds", json=_BUILD)
    assert denied.status_code == 403
    assert denied.json()["detail"] == "没有权限"
    me = target["session"].get("/api/v1/auth/me")
    assert me.status_code == 200, me.text
    assert "executor.run" not in me.json()["permissions"]
    assert _active_sessions(client.app.state.engine, target["account"]["id"]) == 1


def test_patch_and_reset_follow_account_manage(client: TestClient) -> None:
    """Acceptance 14. Readers are forbidden. The machine token records a null actor."""

    target = _login(client, "ada_perm_target", ["reader"])
    roles = (
        ("reader", "ada_perm_reader"),
        ("builder", "ada_perm_builder"),
        ("publisher", "ada_perm_pub"),
    )
    for role, name in roles:
        session = _login(client, name, [role])["session"]
        denied = session.patch(
            f"/api/v1/accounts/{target['account']['id']}",
            json={"status": "disabled"},
        )
        assert denied.status_code == 403, denied.text
        assert denied.json()["detail"] == "没有权限"
        reset = session.post(
            f"/api/v1/accounts/{target['account']['id']}/password",
            json={"password": NEW_PASSWORD},
        )
        assert reset.status_code == 403, reset.text
        assert reset.json()["detail"] == "没有权限"
    anon = TestClient(client.app)
    assert anon.patch(
        f"/api/v1/accounts/{target['account']['id']}",
        json={"status": "disabled"},
    ).status_code == 401
    assert anon.post(
        f"/api/v1/accounts/{target['account']['id']}/password",
        json={"password": NEW_PASSWORD},
    ).status_code == 401
    machine = client.patch(
        f"/api/v1/accounts/{target['account']['id']}",
        json={"role_ids": ["builder"]},
    )
    assert machine.status_code == 200, machine.text
    assert machine.json()["updated_by"] is None
    assert machine.json()["role_ids"] == ["builder"]


def test_last_admin_and_self_are_protected(client: TestClient) -> None:
    """Acceptance 15 and 16. The last admin and a caller's own admin role stay."""

    only = _login(client, "ada_only_admin", ["admin"])
    for payload in ({"status": "disabled"}, {"role_ids": ["reader"]}):
        denied = only["session"].patch(f"/api/v1/accounts/{only['account']['id']}", json=payload)
        assert denied.status_code == 409, denied.text
        assert denied.json()["detail"] == LAST_ADMIN
        machine = client.patch(f"/api/v1/accounts/{only['account']['id']}", json=payload)
        assert machine.status_code == 409, machine.text
        assert machine.json()["detail"] == LAST_ADMIN
    assert _status(client.app.state.engine, only["account"]["id"]) == "active"

    other = _login(client, "ada_other_admin", ["admin"])
    self_off = other["session"].patch(
        f"/api/v1/accounts/{other['account']['id']}",
        json={"status": "disabled"},
    )
    assert self_off.status_code == 409, self_off.text
    assert self_off.json()["detail"] == SELF_ADMIN
    self_role = other["session"].patch(
        f"/api/v1/accounts/{other['account']['id']}",
        json={"role_ids": ["reader"]},
    )
    assert self_role.status_code == 409, self_role.text
    assert self_role.json()["detail"] == SELF_ADMIN
    peer = only["session"].patch(
        f"/api/v1/accounts/{other['account']['id']}",
        json={"status": "disabled"},
    )
    assert peer.status_code == 200, peer.text


def test_concurrent_admin_disables_leave_one_active(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Acceptance 15. Fifty racing disables keep one active admin and never 5xx."""

    left = _login(client, "ada_race_left", ["admin"])
    right = _login(client, "ada_race_right", ["admin"])
    gate: dict[str, threading.Barrier | None] = {"barrier": None}
    real = PostgresStore.update_account

    def wrapped(self: PostgresStore, *args: object, **kwargs: object) -> object:
        current = gate["barrier"]
        if current is not None:
            current.wait(timeout=10)
        return real(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(PostgresStore, "update_account", wrapped)
    for _round in range(50):
        gate["barrier"] = threading.Barrier(2)
        codes: list[int] = []

        def hit(session: TestClient, target: str, found: list[int] = codes) -> None:
            response = session.patch(f"/api/v1/accounts/{target}", json={"status": "disabled"})
            found.append(response.status_code)

        threads = (
            threading.Thread(target=hit, args=(left["session"], right["account"]["id"])),
            threading.Thread(target=hit, args=(right["session"], left["account"]["id"])),
        )
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
            assert not thread.is_alive()
        assert sorted(codes) == [200, 409], codes
        assert all(code < 500 for code in codes)
        assert _active_admins(client.app.state.engine) >= 1
        gate["barrier"] = None
        for account_id in (left["account"]["id"], right["account"]["id"]):
            restored = client.patch(
                f"/api/v1/accounts/{account_id}",
                json={"status": "active"},
            )
            assert restored.status_code == 200, restored.text
        if _active_sessions(client.app.state.engine, left["account"]["id"]) == 0:
            left = _relogin(left)
        if _active_sessions(client.app.state.engine, right["account"]["id"]) == 0:
            right = _relogin(right)


def test_password_reset_and_self_change(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Acceptance 17 and 18. Reset and self-change revoke the sessions they should."""

    caplog.set_level(logging.DEBUG)
    admin = _login(client, "ada_pw_admin", ["admin"])
    target = _login(client, "ada_pw_target", ["builder"])
    extra = TestClient(client.app)
    extra_login = extra.post(
        "/api/v1/auth/login",
        json={"username": "ada_pw_target", "password": LOCAL_PASSWORD},
    )
    assert extra_login.status_code == 200, extra_login.text
    reset = admin["session"].post(
        f"/api/v1/accounts/{target['account']['id']}/password",
        json={"password": NEW_PASSWORD},
    )
    assert reset.status_code == 204, reset.text
    assert reset.content == b""
    assert NEW_PASSWORD not in reset.text
    assert NEW_PASSWORD not in caplog.text
    assert target["session"].get("/api/v1/auth/me").status_code == 401
    assert extra.get("/api/v1/auth/me").status_code == 401
    old = TestClient(client.app).post(
        "/api/v1/auth/login",
        json={"username": "ada_pw_target", "password": LOCAL_PASSWORD},
    )
    assert old.status_code == 401
    fresh = TestClient(client.app).post(
        "/api/v1/auth/login",
        json={"username": "ada_pw_target", "password": NEW_PASSWORD},
    )
    assert fresh.status_code == 200, fresh.text

    other = TestClient(client.app)
    other_login = other.post(
        "/api/v1/auth/login",
        json={"username": "ada_pw_admin", "password": LOCAL_PASSWORD},
    )
    assert other_login.status_code == 200, other_login.text
    self_reset = admin["session"].post(
        f"/api/v1/accounts/{admin['account']['id']}/password",
        json={"password": NEW_PASSWORD},
    )
    assert self_reset.status_code == 204, self_reset.text
    assert admin["session"].get("/api/v1/auth/me").status_code == 200
    assert other.get("/api/v1/auth/me").status_code == 401

    third = TestClient(client.app)
    third_login = third.post(
        "/api/v1/auth/login",
        json={"username": "ada_pw_admin", "password": NEW_PASSWORD},
    )
    assert third_login.status_code == 200, third_login.text
    wrong = admin["session"].post(
        "/api/v1/auth/password",
        json={"current_password": "not-the-current", "new_password": "another-good-pw"},
    )
    assert wrong.status_code == 400
    assert wrong.json()["detail"] == "当前口令不正确"
    changed = admin["session"].post(
        "/api/v1/auth/password",
        json={"current_password": NEW_PASSWORD, "new_password": "final-admin-password"},
    )
    assert changed.status_code == 204, changed.text
    assert "final-admin-password" not in changed.text
    assert "final-admin-password" not in caplog.text
    assert admin["session"].get("/api/v1/auth/me").status_code == 200
    assert third.get("/api/v1/auth/me").status_code == 401
    machine = client.post(
        "/api/v1/auth/password",
        json={"current_password": NEW_PASSWORD, "new_password": "machine-should-fail"},
    )
    assert machine.status_code == 403
    assert machine.json()["detail"] == "机器令牌没有账号"
    assert "machine-should-fail" not in machine.text


def test_sync_accounts_have_no_local_password(
    client: TestClient,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    with OidcDouble() as double:
        app = create_app(_oidc_settings(tmp_path, double))
        with TestClient(app) as synced:
            truncate_domain(app.state.engine)
            first = _complete_oidc(synced, double)
            assert first.status_code == 200, first.text
            account_id = first.json()["account"]["id"]
            with app.state.engine.begin() as connection:
                connection.execute(
                    text("UPDATE accounts SET password_hash = 'stored-hash' WHERE id::text = :id"),
                    {"id": account_id},
                )
            machine = TestClient(app, headers=auth_headers())
            reset = machine.post(
                f"/api/v1/accounts/{account_id}/password",
                json={"password": NEW_PASSWORD},
            )
            assert reset.status_code == 409, reset.text
            assert reset.json()["detail"] == NO_LOCAL_PASSWORD
            assert NEW_PASSWORD not in reset.text
            changed = synced.post(
                "/api/v1/auth/password",
                json={"current_password": LOCAL_PASSWORD, "new_password": NEW_PASSWORD},
            )
            assert changed.status_code == 409, changed.text
            assert changed.json()["detail"] == NO_LOCAL_PASSWORD
            assert NEW_PASSWORD not in changed.text
            assert NEW_PASSWORD not in caplog.text


def test_password_validation_errors_do_not_echo_the_secret(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    admin = _login(client, "ada_echo_admin", ["admin"])
    secret = "S2b-plaintext-secret-value"
    too_short = admin["session"].post(
        f"/api/v1/accounts/{admin['account']['id']}/password",
        json={"password": secret[:6]},
    )
    assert too_short.status_code == 422
    assert secret[:6] not in too_short.text
    current = "S2b-current-secret-value"
    changed = admin["session"].post(
        "/api/v1/auth/password",
        json={"current_password": current, "new_password": secret[:6]},
    )
    assert changed.status_code == 422, changed.text
    assert current not in changed.text
    assert secret[:6] not in changed.text
    assert current not in caplog.text
    assert secret[:6] not in caplog.text
    confirm = "confirm-plaintext-should-not-echo"
    reset = admin["session"].post(
        f"/api/v1/accounts/{admin['account']['id']}/password",
        json={"password": "long-enough-reset", "confirm_password": confirm},
    )
    _assert_confirm_is_not_echoed(reset, confirm, caplog)
    changed_extra = admin["session"].post(
        "/api/v1/auth/password",
        json={
            "current_password": "current-ok",
            "new_password": "long-enough-new",
            "confirm_password": confirm,
        },
    )
    _assert_confirm_is_not_echoed(changed_extra, confirm, caplog)
    folded = "Folded-Password-plaintext"
    folded_change = admin["session"].post(
        "/api/v1/auth/password",
        json={
            "current_password": "current-ok",
            "new_password": "long-enough-new",
            "Confirm_Password": folded,
        },
    )
    _assert_confirm_is_not_echoed(folded_change, folded, caplog)
    empty = admin["session"].patch(f"/api/v1/accounts/{admin['account']['id']}", json={})
    assert empty.status_code == 422
    unknown = admin["session"].patch(
        f"/api/v1/accounts/{admin['account']['id']}",
        json={"role_ids": []},
    )
    assert unknown.status_code == 422


def _assert_confirm_is_not_echoed(response, secret: str, caplog: pytest.LogCaptureFixture) -> None:
    assert response.status_code == 422, response.text
    assert secret not in response.text
    assert secret not in caplog.text
    detail = response.json()["detail"]
    names = [
        name
        for item in detail
        if isinstance(item.get("loc"), list)
        for name in item["loc"]
        if isinstance(name, str)
    ]
    assert any("password" in name.casefold() for name in names)
    for item in detail:
        if "input" not in item:
            continue
        assert item["input"] != secret
        assert secret not in str(item["input"])


def test_disable_between_password_check_and_session_insert(client: TestClient) -> None:
    """Acceptance 19. A disable committed before create_session issues no session."""

    created = client.post(
        "/api/v1/accounts",
        json={
            "username": "ada_gap",
            "display_name": "缝",
            "password": LOCAL_PASSWORD,
            "role_ids": ["reader"],
        },
    )
    assert created.status_code == 201, created.text
    account_id = created.json()["id"]
    store = client.app.state.store
    arrived = threading.Event()
    release = threading.Event()
    real = store.create_session

    def wrapped(account: UUID, token_hash: str, ttl: int) -> object:
        if account == UUID(account_id):
            arrived.set()
            assert release.wait(timeout=10)
        return real(account, token_hash, ttl)

    store.create_session = wrapped  # type: ignore[method-assign]
    outcome: dict[str, object] = {}

    def login() -> None:
        outcome["response"] = TestClient(client.app).post(
            "/api/v1/auth/login",
            json={"username": "ada_gap", "password": LOCAL_PASSWORD},
        )

    worker = threading.Thread(target=login)
    worker.start()
    try:
        assert arrived.wait(timeout=10)
        disabled = client.patch(f"/api/v1/accounts/{account_id}", json={"status": "disabled"})
        assert disabled.status_code == 200, disabled.text
        release.set()
        worker.join(timeout=10)
    finally:
        release.set()
        store.create_session = real  # type: ignore[method-assign]
    assert not worker.is_alive()
    response = outcome["response"]
    assert response.status_code == 401  # type: ignore[attr-defined]
    assert response.json()["detail"] == "账号已停用"  # type: ignore[attr-defined]
    assert _active_sessions(client.app.state.engine, account_id) == 0


def test_create_session_waits_for_the_account_lock(client: TestClient) -> None:
    """Acceptance 19. create_session waits while PATCH holds FOR UPDATE."""

    created = client.post(
        "/api/v1/accounts",
        json={
            "username": "ada_lock",
            "display_name": "锁",
            "password": LOCAL_PASSWORD,
            "role_ids": ["reader"],
        },
    )
    assert created.status_code == 201, created.text
    account_id = created.json()["id"]
    engine = client.app.state.engine
    held = threading.Event()
    release = threading.Event()
    share_started = threading.Event()
    state = {"hold": True}

    def after_cursor_execute(
        _conn: object,
        _cursor: object,
        statement: object,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        sql = " ".join(str(statement).upper().split())
        if state["hold"] and "FOR UPDATE" in sql and "FROM ACCOUNTS" in sql:
            held.set()
            assert release.wait(timeout=10)
            state["hold"] = False

    def before_cursor_execute(
        _conn: object,
        _cursor: object,
        statement: object,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        sql = " ".join(str(statement).upper().split())
        if "FOR SHARE" in sql and "ACCOUNTS" in sql:
            share_started.set()

    event.listen(engine, "after_cursor_execute", after_cursor_execute)
    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    outcome: dict[str, object] = {}

    def patch() -> None:
        outcome["patch"] = client.patch(
            f"/api/v1/accounts/{account_id}",
            json={"status": "disabled"},
        )

    def login() -> None:
        outcome["login"] = TestClient(client.app).post(
            "/api/v1/auth/login",
            json={"username": "ada_lock", "password": LOCAL_PASSWORD},
        )

    patcher = threading.Thread(target=patch)
    patcher.start()
    try:
        assert held.wait(timeout=10)
        signer = threading.Thread(target=login)
        signer.start()
        assert share_started.wait(timeout=10)
        assert signer.is_alive()
        release.set()
        patcher.join(timeout=10)
        signer.join(timeout=10)
    finally:
        release.set()
        event.remove(engine, "after_cursor_execute", after_cursor_execute)
        event.remove(engine, "before_cursor_execute", before_cursor_execute)
    assert not patcher.is_alive()
    assert not signer.is_alive()
    assert outcome["patch"].status_code == 200  # type: ignore[attr-defined]
    login_response = outcome["login"]
    assert login_response.status_code == 401  # type: ignore[attr-defined]
    assert login_response.json()["detail"] == "账号已停用"  # type: ignore[attr-defined]
    assert _active_sessions(engine, account_id) == 0


def test_for_share_blocks_a_plain_status_update(client: TestClient) -> None:
    """Acceptance 19. FOR SHARE, not FOR KEY SHARE, blocks an unlocked UPDATE."""

    created = client.post(
        "/api/v1/accounts",
        json={
            "username": "ada_share",
            "display_name": "共享",
            "password": LOCAL_PASSWORD,
            "role_ids": ["reader"],
        },
    )
    assert created.status_code == 201, created.text
    account_id = created.json()["id"]
    engine = client.app.state.engine
    held = threading.Event()
    release = threading.Event()

    def after_cursor_execute(
        _conn: object,
        _cursor: object,
        statement: object,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        sql = " ".join(str(statement).upper().split())
        if "FOR SHARE" in sql and "ACCOUNTS" in sql:
            held.set()
            assert release.wait(timeout=10)

    event.listen(engine, "after_cursor_execute", after_cursor_execute)
    outcome: dict[str, object] = {}

    def login() -> None:
        outcome["login"] = TestClient(client.app).post(
            "/api/v1/auth/login",
            json={"username": "ada_share", "password": LOCAL_PASSWORD},
        )

    signer = threading.Thread(target=login)
    signer.start()
    updater = threading.Thread(target=_plain_disable, args=(engine, account_id, outcome))
    try:
        assert held.wait(timeout=10)
        updater.start()
        threading.Event().wait(0.3)
        assert updater.is_alive()
        release.set()
        signer.join(timeout=10)
        updater.join(timeout=10)
    finally:
        release.set()
        event.remove(engine, "after_cursor_execute", after_cursor_execute)
    assert not signer.is_alive()
    assert not updater.is_alive()
    assert outcome["login"].status_code == 200  # type: ignore[attr-defined]
    assert outcome.get("updated") is True
    assert _status(engine, account_id) == "disabled"
    assert _active_sessions(engine, account_id) == 1


def test_session_issued_first_is_revoked_by_a_later_disable(client: TestClient) -> None:
    """Acceptance 19. A committed session is revoked when disable commits afterwards."""

    target = _login(client, "ada_later", ["reader"])
    admin = _login(client, "ada_later_admin", ["admin"])
    disabled = admin["session"].patch(
        f"/api/v1/accounts/{target['account']['id']}",
        json={"status": "disabled"},
    )
    assert disabled.status_code == 200, disabled.text
    with client.app.state.engine.connect() as connection:
        revoked = connection.execute(
            text(
                """
                SELECT revoked_at IS NOT NULL
                FROM sessions WHERE account_id::text = :id
                """
            ),
            {"id": target["account"]["id"]},
        ).scalar_one()
    assert revoked is True
    enabled = admin["session"].patch(
        f"/api/v1/accounts/{target['account']['id']}",
        json={"status": "active"},
    )
    assert enabled.status_code == 200, enabled.text
    assert target["session"].get("/api/v1/auth/me").status_code == 401


def test_migration_0012_backfills_and_round_trips(client: TestClient) -> None:
    """Acceptance 20. Head is 0013. 0012 backfill, default, indexes, and downgrade."""

    cfg = _alembic_config(postgres_url())
    script = ScriptDirectory.from_config(cfg)
    assert script.get_heads() == ["0013_build_package_types"]
    revision = script.get_revision("0012_account_lifecycle")
    assert revision is not None
    assert revision.down_revision == "0011_actor_indexes"
    _assert_lifecycle_on_empty_database()

    engine = client.app.state.engine
    planted = str(uuid4())
    try:
        command.downgrade(cfg, "0011_actor_indexes")
        assert _lifecycle_columns(engine) == set()
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO accounts (id, username, display_name, status, created_at)
                    VALUES (:id, :username, '回填', 'active',
                            TIMESTAMPTZ '2026-01-02T03:04:05Z')
                    """
                ),
                {"id": planted, "username": f"back_{uuid4().hex[:8]}"},
            )
        command.upgrade(cfg, "head")
        with engine.connect() as connection:
            same, actor_null = connection.execute(
                text(
                    """
                    SELECT updated_at = created_at, updated_by IS NULL
                    FROM accounts WHERE id = :id
                    """
                ),
                {"id": planted},
            ).one()
        assert same is True
        assert actor_null is True
        _assert_lifecycle_schema(engine)
        raw_id = str(uuid4())
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO accounts (id, username, display_name, status, created_at)
                    VALUES (:id, :username, '默认', 'active',
                            TIMESTAMPTZ '2026-02-02T03:04:05Z')
                    """
                ),
                {"id": raw_id, "username": f"raw_{uuid4().hex[:8]}"},
            )
            filled = connection.execute(
                text("SELECT updated_at IS NOT NULL FROM accounts WHERE id = :id"),
                {"id": raw_id},
            ).scalar_one()
        assert filled is True
        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            diff = compare_metadata(context, Base.metadata)
        assert diff == []
        command.downgrade(cfg, "0011_actor_indexes")
        assert _lifecycle_columns(engine) == set()
        assert _lifecycle_indexes(engine) == set()
        with engine.connect() as connection:
            still = connection.execute(
                text("SELECT display_name FROM accounts WHERE id = :id"),
                {"id": planted},
            ).scalar_one()
        assert still == "回填"
    finally:
        command.upgrade(cfg, "head")
        truncate_domain(engine)


def test_create_paths_write_updated_at(client: TestClient, tmp_path: Path) -> None:
    """Acceptance 24. The three insert paths set updated_at and leave updated_by null."""

    created = client.post(
        "/api/v1/accounts",
        json={"username": "ada_stamp", "display_name": "戳", "role_ids": ["reader"]},
    )
    assert created.status_code == 201, created.text
    _assert_stamp(client.app.state.engine, "ada_stamp")

    with OidcDouble() as double:
        app = create_app(_oidc_settings(tmp_path, double))
        with TestClient(app) as synced:
            truncate_domain(app.state.engine)
            first = _complete_oidc(synced, double)
            assert first.status_code == 200, first.text
            _assert_stamp(app.state.engine, first.json()["account"]["username"])


def test_docs_describe_account_lifecycle() -> None:
    """Acceptance 22. README and DEPLOY name the routes and the protection rules."""

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    deploy = (ROOT / "docs" / "DEPLOY.md").read_text(encoding="utf-8")
    for document in (readme, deploy):
        assert "PATCH /accounts/{id}" in document or "PATCH /api/v1/accounts/{id}" in document
        assert "/password" in document
        assert LAST_ADMIN in document
        assert SELF_ADMIN in document
        assert NO_LOCAL_PASSWORD in document
        assert "updated_by" in document
        assert "改角色不撤销" in document
        assert "重新启用不会恢复" in document
        assert "用户信息同步" in document
        assert "联邦" not in document
        assert "federation" not in document.casefold()
    assert "退出码 5" in deploy or "退出码：`5`" in deploy or "`5`" in deploy


def _login(client: TestClient, username: str, role_ids: list[str]) -> dict[str, object]:
    created = client.post(
        "/api/v1/accounts",
        json={
            "username": username,
            "display_name": username,
            "password": LOCAL_PASSWORD,
            "role_ids": role_ids,
        },
    )
    assert created.status_code == 201, created.text
    session = TestClient(client.app)
    logged = session.post(
        "/api/v1/auth/login",
        json={"username": username, "password": LOCAL_PASSWORD},
    )
    assert logged.status_code == 200, logged.text
    return {
        "session": session,
        "token": logged.json()["token"],
        "account": created.json(),
        "username": username,
    }


def _relogin(previous: dict[str, object]) -> dict[str, object]:
    session = TestClient(previous["session"].app)  # type: ignore[attr-defined]
    logged = session.post(
        "/api/v1/auth/login",
        json={"username": previous["username"], "password": LOCAL_PASSWORD},
    )
    assert logged.status_code == 200, logged.text
    previous["session"] = session
    previous["token"] = logged.json()["token"]
    return previous


def _plain_disable(engine: Engine, account_id: str, outcome: dict[str, object]) -> None:
    with engine.connect() as connection:
        connection.execute(text("SET lock_timeout = '5s'"))
        connection.execute(
            text("UPDATE accounts SET status = 'disabled' WHERE id = :id"),
            {"id": account_id},
        )
        connection.commit()
    outcome["updated"] = True


def _parsed(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _active_sessions(engine: Engine, account_id: str) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                text(
                    """
                    SELECT count(*) FROM sessions
                    WHERE account_id::text = :id AND revoked_at IS NULL
                    """
                ),
                {"id": account_id},
            ).scalar_one()
        )


def _status(engine: Engine, account_id: str) -> str:
    with engine.connect() as connection:
        return str(
            connection.execute(
                text("SELECT status FROM accounts WHERE id::text = :id"),
                {"id": account_id},
            ).scalar_one()
        )


def _active_admins(engine: Engine) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                text(
                    """
                    SELECT count(DISTINCT a.id)
                    FROM accounts a
                    JOIN account_roles ar ON ar.account_id = a.id
                    WHERE a.status = 'active' AND ar.role_id = 'admin'
                    """
                )
            ).scalar_one()
        )


def _assert_stamp(engine: Engine, username: str) -> None:
    with engine.connect() as connection:
        same, actor_null = connection.execute(
            text(
                """
                SELECT updated_at = created_at, updated_by IS NULL
                FROM accounts WHERE username = :username
                """
            ),
            {"username": username},
        ).one()
    assert same is True
    assert actor_null is True


def _lifecycle_columns(engine: Engine) -> set[str]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'accounts'
                  AND column_name IN ('updated_at', 'updated_by')
                """
            )
        ).scalars()
        return set(rows)


def _lifecycle_indexes(engine: Engine) -> set[str]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT indexname FROM pg_indexes
                WHERE indexname IN ('ix_accounts_updated_by', 'ix_sessions_account_id')
                """
            )
        ).scalars()
        return set(rows)


def _assert_lifecycle_schema(engine: Engine) -> None:
    assert _lifecycle_columns(engine) == {"updated_at", "updated_by"}
    assert _lifecycle_indexes(engine) == {"ix_accounts_updated_by", "ix_sessions_account_id"}
    with engine.connect() as connection:
        nullable, default = connection.execute(
            text(
                """
                SELECT is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'accounts'
                  AND column_name = 'updated_at'
                """
            )
        ).one()
        rule = connection.execute(
            text(
                """
                SELECT confdeltype FROM pg_constraint
                WHERE conname = 'fk_accounts_updated_by_accounts'
                """
            )
        ).scalar_one()
    assert nullable == "NO"
    assert default is not None and "now()" in str(default)
    assert rule == "n"


def _assert_lifecycle_on_empty_database() -> None:
    name = f"kiln_s2b_{uuid4().hex[:12]}"
    admin = create_db_engine(postgres_url()).execution_options(isolation_level="AUTOCOMMIT")
    url = make_url(postgres_url()).set(database=name).render_as_string(hide_password=False)
    empty: Engine | None = None
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
        upgrade_database(url)
        empty = create_db_engine(url)
        _assert_lifecycle_schema(empty)
        command.downgrade(_alembic_config(url), "0011_actor_indexes")
        assert _lifecycle_columns(empty) == set()
        command.upgrade(_alembic_config(url), "head")
        _assert_lifecycle_schema(empty)
        assert ScriptDirectory.from_config(_alembic_config(url)).get_heads() == [
            "0013_build_package_types"
        ]
    finally:
        if empty is not None:
            empty.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
