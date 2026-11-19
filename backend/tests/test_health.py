import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from kiln import __version__
from kiln.main import DESCRIPTION

ROOT = Path(__file__).resolve().parents[2]
_OK = {"status": "ok", "service": "kiln", "version": __version__}
_DB_UNAVAILABLE = {"detail": "数据库不可用"}
_DSN = "postgresql://kiln:ready-probe-secret@127.0.0.1:9/kiln"


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "kiln", "version": "0.1.0"}
    assert response.json() == _OK


def test_root_points_at_health(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert body["health"] == "/health"
    assert body["ready"] == "/ready"
    assert body["docs"] == "/docs"
    assert body["service"] == "kiln"


def test_cors_preflight_allows_patch_and_put() -> None:
    """Acceptance 23. PATCH and PUT preflight succeeds only for a configured origin."""

    from uuid import uuid4

    from kiln.main import create_app
    from tests.support import app_settings

    app = create_app(app_settings(kiln_cors_origins="http://localhost:5173"))
    account_path = f"/api/v1/accounts/{uuid4()}"
    with TestClient(app) as client:
        allowed = client.options(
            account_path,
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "PATCH",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        rejected = client.options(
            account_path,
            headers={
                "Origin": "http://evil.example",
                "Access-Control-Request-Method": "PATCH",
                "Access-Control-Request-Headers": "content-type",
            },
        )
    methods = allowed.headers["access-control-allow-methods"]
    assert allowed.status_code == 200
    assert "PATCH" in methods
    assert "PUT" in methods
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert rejected.status_code == 400
    assert "access-control-allow-origin" not in rejected.headers


def test_cors_allows_configured_console_origin() -> None:
    from kiln.main import create_app
    from tests.support import app_settings

    app = create_app(app_settings(kiln_cors_origins="http://localhost:5173"))
    with TestClient(app) as client:
        allowed = client.get("/health", headers={"Origin": "http://localhost:5173"})
        rejected = client.get("/health", headers={"Origin": "http://evil.example"})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "access-control-allow-origin" not in rejected.headers


def test_outline_paths_are_published(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    expected = [
        "/health",
        "/ready",
        "/api/v1/accounts",
        "/api/v1/accounts/{account_id}",
        "/api/v1/accounts/{account_id}/password",
        "/api/v1/auth/password",
        "/api/v1/rbac/permissions",
        "/api/v1/rbac/roles",
        "/api/v1/artifacts",
        "/api/v1/artifacts/{artifact_id}",
        "/api/v1/artifacts/{artifact_id}/versions",
        "/api/v1/artifacts/{artifact_id}/versions/{version}/download-reservations",
        "/api/v1/retrieve/{reservation_id}",
        "/api/v1/skills",
        "/api/v1/skills/{skill_id}",
        "/api/v1/skills/{skill_id}/submit-review",
        "/api/v1/skills/{skill_id}/publish",
        "/api/v1/llm/provider",
        "/api/v1/llm/chat",
        "/api/v1/dependency-proxy",
        "/api/v1/auth/login",
        "/api/v1/auth/logout",
        "/api/v1/auth/me",
        "/api/v1/auth/sync",
        "/api/v1/auth/oidc/start",
        "/api/v1/auth/oidc/callback",
    ]
    missing = [path for path in expected if path not in paths]
    assert missing == []
    assert "security" not in paths["/ready"]["get"]
    assert paths["/ready"]["get"]["summary"] == "就绪检查"
    assert "/health 与 /ready 与 /docs 不接受写入" in DESCRIPTION
    assert "SELECT 1" in DESCRIPTION
    assert "数据库不可用" in DESCRIPTION


def test_ready_when_database_is_up(client: TestClient) -> None:
    """Acceptance 2 and 4. Ready matches health and ignores unconfigured dependencies."""

    client.headers.pop("Authorization", None)
    statements: list[str] = []

    def before_cursor_execute(
        _conn: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(statement)

    event.listen(client.app.state.engine, "before_cursor_execute", before_cursor_execute)
    try:
        ready = client.get("/ready")
        health = client.get("/health")
    finally:
        event.remove(client.app.state.engine, "before_cursor_execute", before_cursor_execute)

    assert ready.status_code == 200
    assert ready.json() == _OK
    assert health.json() == ready.json()
    assert client.app.state.settings.llm_provider == "off"
    assert client.app.state.jenkins is None
    assert client.app.state.nexus is None
    assert client.app.state.git_source is None
    timeout_at = next(
        index for index, statement in enumerate(statements) if "statement_timeout" in statement
    )
    assert "2s" in statements[timeout_at]
    assert any("SELECT 1" in statement for statement in statements[timeout_at + 1 :])
    assert not any("alembic" in statement.casefold() for statement in statements)


class _Connection:
    def __init__(self, error: BaseException) -> None:
        self._error = error

    def __enter__(self) -> "_Connection":
        return self

    def __exit__(self, *_args: object) -> bool:
        return False

    def execute(self, *_args: object, **_kwargs: object) -> None:
        raise self._error


class _Engine:
    def __init__(self, error: BaseException, *, fail_connect: bool) -> None:
        self._error = error
        self._fail_connect = fail_connect

    def connect(self) -> _Connection:
        if self._fail_connect:
            raise self._error
        return _Connection(self._error)


@pytest.mark.parametrize(
    ("fail_connect", "error"),
    [
        (True, OperationalError("connect", {}, Exception(_DSN))),
        (False, OperationalError("SELECT 1", {}, Exception(f"query failed {_DSN}"))),
        (False, TimeoutError(_DSN)),
    ],
)
def test_ready_503_hides_database_failures(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
    fail_connect: bool,
    error: BaseException,
) -> None:
    """Acceptance 1 and 3. Health stays up; ready failure hides the connection string."""

    caplog.set_level(logging.DEBUG)
    client.app.state.engine = _Engine(error, fail_connect=fail_connect)
    ready = client.get("/ready")
    health = client.get("/health")
    assert ready.status_code == 503
    assert ready.json() == _DB_UNAVAILABLE
    assert _DSN not in ready.text
    assert "ready-probe-secret" not in ready.text
    assert "OperationalError" not in ready.text
    assert "TimeoutError" not in ready.text
    assert health.status_code == 200
    assert health.json() == _OK
    assert _DSN not in caplog.text
    assert "ready-probe-secret" not in caplog.text
    assert not any(record.exc_info for record in caplog.records)


def test_compose_healthcheck_stays_on_health_and_proxies_ready() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    nginx = (ROOT / "console" / "nginx.conf").read_text(encoding="utf-8")
    vite = (ROOT / "console" / "vite.config.js").read_text(encoding="utf-8")
    vue = (ROOT / "console" / "src" / "App.vue").read_text(encoding="utf-8")
    assert "http://127.0.0.1:8000/health" in compose
    assert "/ready" not in compose
    assert "location /health {" in nginx
    assert "location /ready {" in nginx
    assert '"/health"' in vite
    assert '"/ready"' in vite
    assert 'fetch("/health"' in vue
    assert "/ready" not in vue
    assert "topbar-right" in vue


def test_backup_docs_name_real_services_and_ready() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    deploy = (ROOT / "docs" / "DEPLOY.md").read_text(encoding="utf-8")
    for document in (readme, deploy):
        assert "/ready" in document
        assert "数据库不可用" in document
        assert "联邦" not in document
        assert "federation" not in document.casefold()
    assert "docs/DEPLOY.md#备份与恢复" in readme
    for name in (
        "postgres",
        "kiln-postgres-data",
        "kiln-artifact-data",
        "pg_dump",
        "pg_restore",
        "/var/lib/postgresql/data",
        "/var/lib/kiln/artifacts",
        "down -v",
        ".env",
        "docker volume ls",
    ):
        assert name in deploy, name
    assert "标准输出" in deploy
    assert "标准错误" in deploy
    assert "curl -fsS http://127.0.0.1:8000/ready" in deploy
    assert "把 `/health` 当成全量依赖检查" in deploy
    failures = deploy.split("## 常见失败", 1)[1]
    assert "/ready" in failures
    assert "查看 API 容器日志" in deploy
    assert failures.split("查看 API 容器日志", 1)[0].count("/ready") >= 1
