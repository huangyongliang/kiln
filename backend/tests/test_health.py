from fastapi.testclient import TestClient


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "kiln", "version": "0.1.0"}


def test_root_points_at_health(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["health"] == "/health"


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
