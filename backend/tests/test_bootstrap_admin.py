"""S2a acceptance for kiln-admin bootstrap-admin."""

import io
import logging
import sys
import threading
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url

from kiln.cli import DB_UNREACHABLE, main
from kiln.config import API_TOKEN_ERROR, BUILD_PENDING_TIMEOUT_ERROR
from kiln.db.engine import create_db_engine
from kiln.db.migrate import upgrade_database
from kiln.db.seed import seed_rbac
from kiln.db.store import PostgresStore
from tests.support import PROCESS_API_TOKEN, TEST_API_TOKEN, postgres_url

PASSWORD = "s2a-bootstrap-password"
ROOT = Path(__file__).resolve().parents[2]


class _RepeatStdin:
    def __init__(self, line: str) -> None:
        self.line = line

    def isatty(self) -> bool:
        return False

    def readline(self) -> str:
        return self.line


def _prepare(monkeypatch: pytest.MonkeyPatch, password: str = PASSWORD + "\n") -> None:
    monkeypatch.setenv("DATABASE_URL", postgres_url())
    monkeypatch.setattr(sys, "stdin", _RepeatStdin(password))


def _admin_rows(engine: object) -> list[tuple[object, ...]]:
    with engine.connect() as connection:  # type: ignore[attr-defined]
        return list(
            connection.execute(
                text(
                    """
                    SELECT a.username, a.status, a.password_hash, ar.role_id
                    FROM accounts a
                    JOIN account_roles ar ON ar.account_id = a.id
                    ORDER BY a.username, ar.position
                    """
                )
            )
        )


def _account_count(engine: object) -> int:
    with engine.connect() as connection:  # type: ignore[attr-defined]
        return int(connection.execute(text("SELECT count(*) FROM accounts")).scalar_one())


def test_bootstrap_admin_creates_then_refuses(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Acceptance 5. Empty database, second run, secrets stay out of output."""

    caplog.set_level(logging.DEBUG)
    _prepare(monkeypatch)
    monkeypatch.setenv("KILN_ADMIN_PASSWORD", "env-password-must-not-be-used")
    monkeypatch.setenv("PASSWORD", "env-password-must-not-be-used")
    code = main(
        [
            "bootstrap-admin",
            "--username",
            "ada_boot",
            "--display-name",
            "Ada",
            "--email",
            "ada@example.com",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out.strip() == "已创建管理员 ada_boot"
    assert PASSWORD not in captured.out
    assert PASSWORD not in captured.err
    assert PASSWORD not in caplog.text
    assert TEST_API_TOKEN not in captured.out + captured.err + caplog.text
    assert PROCESS_API_TOKEN not in captured.out + captured.err + caplog.text
    with client.app.state.engine.connect() as connection:
        same, actor_null = connection.execute(
            text(
                """
                SELECT updated_at = created_at, updated_by IS NULL
                FROM accounts WHERE username = 'ada_boot'
                """
            )
        ).one()
    assert same is True
    assert actor_null is True
    rows = _admin_rows(client.app.state.engine)
    assert len(rows) == 1
    username, status, password_hash, role_id = rows[0]
    assert username == "ada_boot"
    assert status == "active"
    assert role_id == "admin"
    assert str(password_hash).startswith("$argon2")
    logged = client.post(
        "/api/v1/auth/login",
        json={"username": "ada_boot", "password": PASSWORD},
    )
    assert logged.status_code == 200, logged.text
    assert logged.json()["account"]["role_ids"] == ["admin"]
    again = main(["bootstrap-admin", "--username", "ada_other", "--display-name", "Other"])
    again_out = capsys.readouterr()
    assert again == 3
    assert again_out.out.strip() == "已存在管理员，未做改动"
    assert _account_count(client.app.state.engine) == 1
    assert PASSWORD not in again_out.out
    assert PASSWORD not in again_out.err
    assert PASSWORD not in caplog.text


def test_bootstrap_refuses_a_disabled_admin(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Acceptance 5. An admin created by POST /accounts blocks bootstrap even when disabled."""

    created = client.post(
        "/api/v1/accounts",
        json={
            "username": "ada_posted",
            "display_name": "Posted",
            "password": PASSWORD,
            "role_ids": ["admin"],
        },
    )
    assert created.status_code == 201, created.text
    with client.app.state.engine.begin() as connection:
        connection.execute(
            text("UPDATE accounts SET status = 'disabled' WHERE username = 'ada_posted'")
        )
    _prepare(monkeypatch)
    code = main(["bootstrap-admin", "--username", "ada_second", "--display-name", "Second"])
    captured = capsys.readouterr()
    assert code == 3
    assert captured.out.strip() == "已存在管理员，未做改动"
    assert _account_count(client.app.state.engine) == 1
    assert PASSWORD not in captured.out + captured.err


def test_bootstrap_rejects_a_short_password(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    short = "short"
    _prepare(monkeypatch, short + "\n")
    code = main(["bootstrap-admin", "--username", "ada_short", "--display-name", "Short"])
    captured = capsys.readouterr()
    assert code == 2
    assert "口令长度须为 8–128 个字符" in captured.err
    assert _account_count(client.app.state.engine) == 0
    assert short not in captured.out
    assert short not in captured.err
    assert short not in caplog.text


def test_bootstrap_rejects_a_taken_non_admin_username(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    created = client.post(
        "/api/v1/accounts",
        json={"username": "ada_reader", "display_name": "Reader", "role_ids": ["reader"]},
    )
    assert created.status_code == 201, created.text
    _prepare(monkeypatch)
    code = main(["bootstrap-admin", "--username", "ada_reader", "--display-name", "Reader"])
    captured = capsys.readouterr()
    assert code == 4
    assert captured.out.strip() == "用户名已存在"
    assert _account_count(client.app.state.engine) == 1
    assert PASSWORD not in captured.out + captured.err


def test_bootstrap_password_comes_from_the_tty_or_stdin_only(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "argv-password-should-not-echo"
    _prepare(monkeypatch, PASSWORD + "\n")
    refused = main(
        [
            "bootstrap-admin",
            "--username",
            "ada_argv",
            "--display-name",
            "Argv",
            "--password",
            secret,
        ]
    )
    captured = capsys.readouterr()
    assert refused == 2
    assert secret not in captured.out
    assert secret not in captured.err
    assert _account_count(client.app.state.engine) == 0

    class _Tty:
        def isatty(self) -> bool:
            return True

        def readline(self) -> str:
            raise AssertionError("tty bootstrap read stdin")

    monkeypatch.setattr(sys, "stdin", _Tty())
    answers = iter([PASSWORD, "other-password-value"])
    monkeypatch.setattr("kiln.cli.getpass", lambda prompt="": next(answers))
    mismatched = main(["bootstrap-admin", "--username", "ada_tty", "--display-name", "Tty"])
    mismatch_out = capsys.readouterr()
    assert mismatched == 2
    assert "两次输入的口令不一致" in mismatch_out.err
    assert PASSWORD not in mismatch_out.out + mismatch_out.err
    assert _account_count(client.app.state.engine) == 0

    answers = iter([PASSWORD, PASSWORD])
    monkeypatch.setattr("kiln.cli.getpass", lambda prompt="": next(answers))
    created = main(["bootstrap-admin", "--username", "ada_tty", "--display-name", "Tty"])
    created_out = capsys.readouterr()
    assert created == 0
    assert created_out.out.strip() == "已创建管理员 ada_tty"
    assert PASSWORD not in created_out.out + created_out.err


def test_bootstrap_does_not_migrate_when_the_token_is_unsafe(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("KILN_API_TOKEN", raising=False)
    monkeypatch.setenv("DATABASE_URL", postgres_url())
    monkeypatch.setattr(sys, "stdin", io.StringIO(PASSWORD + "\n"))

    def fail_upgrade(_url: str) -> None:
        raise AssertionError("upgrade ran before settings were accepted")

    monkeypatch.setattr("kiln.cli.upgrade_database", fail_upgrade)
    code = main(["bootstrap-admin", "--username", "ada_token", "--display-name", "Token"])
    captured = capsys.readouterr()
    assert code == 2
    assert API_TOKEN_ERROR in captured.err
    assert "kiln-local 是被拒绝的示例值" in captured.err
    assert "Traceback" not in captured.err
    assert "input_value" not in captured.err
    assert PASSWORD not in captured.out + captured.err
    assert _account_count(client.app.state.engine) == 0

    monkeypatch.setenv("KILN_API_TOKEN", "short-token-value")
    echoed = main(["bootstrap-admin", "--username", "ada_short_token", "--display-name", "Token"])
    echoed_out = capsys.readouterr()
    assert echoed == 2
    assert "short-token-value" not in echoed_out.out + echoed_out.err
    assert "Traceback" not in echoed_out.err


def test_concurrent_bootstrap_creates_one_admin(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Acceptance 6. Two threads pass a barrier inside bootstrap; exactly one admin."""

    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("DATABASE_URL", postgres_url())
    monkeypatch.setattr(sys, "stdin", _RepeatStdin(PASSWORD + "\n"))
    upgrade_database(postgres_url())
    seed_rbac(client.app.state.engine)
    gate = threading.Lock()

    def serialized_upgrade(url: str) -> None:
        with gate:
            upgrade_database(url)

    def serialized_seed(engine: object) -> None:
        with gate:
            seed_rbac(engine)  # type: ignore[arg-type]

    monkeypatch.setattr("kiln.cli.upgrade_database", serialized_upgrade)
    monkeypatch.setattr("kiln.cli.seed_rbac", serialized_seed)
    barrier = threading.Barrier(2)
    real = PostgresStore.bootstrap_admin

    def raced(self: PostgresStore, payload: object) -> object:
        barrier.wait(timeout=10)
        return real(self, payload)  # type: ignore[arg-type]

    monkeypatch.setattr(PostgresStore, "bootstrap_admin", raced)
    outcome: dict[str, object] = {}

    def run(name: str) -> None:
        try:
            outcome[name] = main(
                ["bootstrap-admin", "--username", name, "--display-name", name]
            )
        except Exception as exc:  # pragma: no cover - failure detail
            outcome[name] = exc

    left = threading.Thread(target=run, args=("ada_race_a",))
    right = threading.Thread(target=run, args=("ada_race_b",))
    left.start()
    right.start()
    left.join(timeout=30)
    right.join(timeout=30)
    assert not left.is_alive()
    assert not right.is_alive()
    assert not isinstance(outcome["ada_race_a"], Exception), outcome["ada_race_a"]
    assert not isinstance(outcome["ada_race_b"], Exception), outcome["ada_race_b"]
    codes = {outcome["ada_race_a"], outcome["ada_race_b"]}
    assert codes == {0, 3}
    assert _account_count(client.app.state.engine) == 1
    rows = _admin_rows(client.app.state.engine)
    assert len(rows) == 1
    assert rows[0][1] == "active"
    assert rows[0][3] == "admin"
    captured = capsys.readouterr()
    assert PASSWORD not in captured.out
    assert PASSWORD not in captured.err
    assert PASSWORD not in caplog.text
    script = (ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8")
    assert 'kiln-admin = "kiln.cli:main"' in script


def test_bootstrap_names_the_failed_settings_rule(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A timeout error is not reported as a token error."""

    _prepare(monkeypatch)
    monkeypatch.setenv("KILN_BUILD_PENDING_TIMEOUT_SECONDS", "59")

    def fail_upgrade(_url: str) -> None:
        raise AssertionError("upgrade ran after settings were rejected")

    monkeypatch.setattr("kiln.cli.upgrade_database", fail_upgrade)
    code = main(["bootstrap-admin", "--username", "ada_timeout", "--display-name", "Timeout"])
    captured = capsys.readouterr()
    assert code == 2
    assert BUILD_PENDING_TIMEOUT_ERROR in captured.err
    assert API_TOKEN_ERROR not in captured.err
    assert "59" not in captured.err
    assert "Traceback" not in captured.err

    monkeypatch.setenv("KILN_BUILD_PENDING_TIMEOUT_SECONDS", "1800")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    other = main(["bootstrap-admin", "--username", "ada_llm", "--display-name", "Llm"])
    other_out = capsys.readouterr()
    assert other == 2
    assert "LLM_BASE_URL" in other_out.err
    assert API_TOKEN_ERROR not in other_out.err
    assert "Traceback" not in other_out.err


def test_bootstrap_unreachable_database_has_a_documented_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://kiln:secret-db@127.0.0.1:1/kiln")
    monkeypatch.setattr(sys, "stdin", _RepeatStdin(PASSWORD + "\n"))
    code = main(["bootstrap-admin", "--username", "ada_down", "--display-name", "Down"])
    captured = capsys.readouterr()
    assert code == 5
    assert DB_UNREACHABLE in captured.err
    assert "Traceback" not in captured.err
    assert "secret-db" not in captured.out + captured.err
    assert PASSWORD not in captured.out + captured.err


def test_concurrent_bootstrap_retries_a_migrate_race(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Two empty-database runs share migrate and seed. The loser exits 3."""

    name = f"kiln_s2b_{uuid4().hex[:12]}"
    admin = create_db_engine(postgres_url()).execution_options(isolation_level="AUTOCOMMIT")
    url = make_url(postgres_url()).set(database=name).render_as_string(hide_password=False)
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        monkeypatch.setenv("DATABASE_URL", url)
        monkeypatch.setattr(sys, "stdin", _RepeatStdin(PASSWORD + "\n"))
        barrier = threading.Barrier(2)
        real = PostgresStore.bootstrap_admin

        def raced(self: PostgresStore, payload: object) -> object:
            barrier.wait(timeout=60)
            return real(self, payload)  # type: ignore[arg-type]

        monkeypatch.setattr(PostgresStore, "bootstrap_admin", raced)
        outcome: dict[str, object] = {}

        def run(username: str) -> None:
            try:
                outcome[username] = main(
                    ["bootstrap-admin", "--username", username, "--display-name", username]
                )
            except Exception as exc:  # pragma: no cover - failure detail
                outcome[username] = exc

        left = threading.Thread(target=run, args=("ada_mig_a",))
        right = threading.Thread(target=run, args=("ada_mig_b",))
        left.start()
        right.start()
        left.join(timeout=120)
        right.join(timeout=120)
        assert not left.is_alive()
        assert not right.is_alive()
        assert not isinstance(outcome["ada_mig_a"], Exception), outcome["ada_mig_a"]
        assert not isinstance(outcome["ada_mig_b"], Exception), outcome["ada_mig_b"]
        assert {outcome["ada_mig_a"], outcome["ada_mig_b"]} == {0, 3}
        captured = capsys.readouterr()
        assert "Traceback" not in captured.err
        assert PASSWORD not in captured.out + captured.err
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
