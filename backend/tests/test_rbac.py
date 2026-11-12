"""Permission gates for job, product-build, and chat writes.

These cases do not need Jenkins, Gitea, or an OIDC issuer. Unconfigured
Jenkins and git stay 503 only after the permission check has passed.
"""

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text

from kiln.db.seed import seed_rbac
from kiln.main import create_app
from tests.support import app_settings, auth_headers, truncate_domain

LOCAL_PASSWORD = "correct-horse-1"

_JOB = {"marker": "gated"}
_BUILD = {
    "repository": "ada/demo",
    "ref": "main",
    "artifact_name": "gated-pkg",
    "version": "1",
    "package_type": "generic",
}
_CHAT = {"messages": [{"role": "user", "content": "你好"}]}


def test_anonymous_writes_are_unauthorized_and_insert_nothing(client: TestClient) -> None:
    anon = TestClient(client.app)
    assert anon.post("/api/v1/jobs", json=_JOB).status_code == 401
    assert anon.post("/api/v1/builds", json=_BUILD).status_code == 401
    assert anon.post("/api/v1/llm/chat", json=_CHAT).status_code == 401
    assert _row_counts(client.app.state.engine) == (0, 0)


def test_reader_is_forbidden_before_executor_configuration(client: TestClient) -> None:
    reader = _login(client, "ada_reader", ["reader"])
    for path, payload in (
        ("/api/v1/jobs", _JOB),
        ("/api/v1/builds", _BUILD),
        ("/api/v1/llm/chat", _CHAT),
    ):
        denied = reader.post(path, json=payload)
        assert denied.status_code == 403, path
        assert denied.json()["detail"] == "没有权限"
        assert "JENKINS" not in denied.text
        assert "模型未启用" not in denied.text
    assert _row_counts(client.app.state.engine) == (0, 0)
    assert reader.get("/api/v1/jobs").status_code == 503
    assert reader.get("/api/v1/builds").status_code == 503
    assert reader.get("/api/v1/llm/provider").status_code == 200
    assert reader.get("/api/v1/executor").status_code == 200


def test_builder_passes_the_gate_and_mock_chat_succeeds(tmp_path: Path) -> None:
    app = create_app(
        app_settings(
            llm_provider="mock",
            kiln_artifact_root=str(tmp_path / "artifacts"),
        )
    )
    with TestClient(app, headers=auth_headers()) as client:
        truncate_domain(app.state.engine)
        sessions = {
            role_id: _login(client, f"ada_{role_id}", [role_id])
            for role_id in ("builder", "publisher", "admin")
        }
        for role_id, session in sessions.items():
            job = session.post("/api/v1/jobs", json=_JOB)
            assert job.status_code == 503, role_id
            assert "JENKINS_URL" in job.json()["detail"]
            build = session.post("/api/v1/builds", json=_BUILD)
            assert build.status_code == 503, role_id
            assert "没有权限" not in build.text
        chat = sessions["builder"].post("/api/v1/llm/chat", json=_CHAT)
        assert chat.status_code == 200
        assert chat.json()["provider"] == "mock"
        assert "你好" in chat.json()["content"]
        machine_chat = client.post("/api/v1/llm/chat", json=_CHAT)
        assert machine_chat.status_code == 200
        machine_job = client.post("/api/v1/jobs", json=_JOB)
        machine_build = client.post("/api/v1/builds", json=_BUILD)
        assert machine_job.status_code == 503
        assert machine_build.status_code == 503
        assert _row_counts(app.state.engine) == (0, 0)


def test_machine_token_is_not_forbidden_when_unconfigured(client: TestClient) -> None:
    job = client.post("/api/v1/jobs", json=_JOB)
    build = client.post("/api/v1/builds", json=_BUILD)
    chat = client.post("/api/v1/llm/chat", json=_CHAT)
    assert job.status_code == 503
    assert "JENKINS_URL" in job.json()["detail"]
    assert build.status_code == 503
    assert chat.status_code == 503
    assert "没有权限" not in job.text
    assert "没有权限" not in build.text
    assert "没有权限" not in chat.text
    assert _row_counts(client.app.state.engine) == (0, 0)


def test_permission_catalog_includes_executor_run(client: TestClient) -> None:
    permissions = client.get("/api/v1/rbac/permissions")
    assert permissions.status_code == 200
    described = {item["code"]: item["description"] for item in permissions.json()["items"]}
    assert described["executor.run"] == "在兼容执行器上发起任务或产品构建"
    roles = {
        role["id"]: set(role["permissions"])
        for role in client.get("/api/v1/rbac/roles").json()["items"]
    }
    assert "executor.run" not in roles["reader"]
    assert "agent.qa" not in roles["reader"]
    for role_id in ("builder", "publisher", "admin"):
        assert "executor.run" in roles[role_id]
        assert "agent.qa" in roles[role_id]


def test_seed_rbac_upserts_new_codes_onto_an_existing_catalog(client: TestClient) -> None:
    engine = client.app.state.engine
    with engine.begin() as connection:
        removed = connection.execute(
            text("DELETE FROM rbac_role_permissions WHERE permission_code = 'executor.run'")
        )
        assert removed.rowcount >= 1
        deleted = connection.execute(
            text("DELETE FROM rbac_permissions WHERE code = 'executor.run'")
        )
        assert deleted.rowcount == 1
        updated = connection.execute(
            text("UPDATE rbac_permissions SET description = '旧说明' WHERE code = 'agent.qa'")
        )
        assert updated.rowcount == 1
        remaining = connection.execute(text("SELECT count(*) FROM rbac_permissions")).scalar_one()
    assert remaining >= 1
    before = client.get("/api/v1/rbac/permissions").json()["items"]
    assert all(item["code"] != "executor.run" for item in before)
    seed_rbac(engine)
    permissions = client.get("/api/v1/rbac/permissions").json()["items"]
    described = {item["code"]: item["description"] for item in permissions}
    assert described["executor.run"] == "在兼容执行器上发起任务或产品构建"
    assert described["agent.qa"] == "使用平台内问答"
    roles = {
        role["id"]: role["permissions"]
        for role in client.get("/api/v1/rbac/roles").json()["items"]
    }
    assert "executor.run" not in roles["reader"]
    assert "executor.run" in roles["builder"]
    assert "executor.run" in roles["publisher"]
    assert "executor.run" in roles["admin"]
    assert roles["builder"].count("executor.run") == 1


def _login(client: TestClient, username: str, role_ids: list[str]) -> TestClient:
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
    logged_in = session.post(
        "/api/v1/auth/login",
        json={"username": username, "password": LOCAL_PASSWORD},
    )
    assert logged_in.status_code == 200, logged_in.text
    assert logged_in.json()["account"]["role_ids"] == role_ids
    return session


def _row_counts(engine) -> tuple[int, int]:
    with engine.connect() as connection:
        jobs = connection.execute(text("SELECT count(*) FROM executor_jobs")).scalar_one()
        builds = connection.execute(text("SELECT count(*) FROM product_builds")).scalar_one()
    return jobs, builds
