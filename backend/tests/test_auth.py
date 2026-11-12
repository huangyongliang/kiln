"""Unauthenticated clients cannot read or change durable domain rows."""

from fastapi.testclient import TestClient
from sqlalchemy import text

from kiln.config import LOCAL_API_TOKEN
from kiln.main import create_app
from tests.support import app_settings

SHA = "cd" * 32


def test_anonymous_requests_do_not_change_durable_rows(client: TestClient) -> None:
    created = client.post(
        "/api/v1/accounts",
        json={"username": "gated_user", "display_name": "门禁"},
    )
    assert created.status_code == 201
    artifact = client.post(
        "/api/v1/artifacts",
        json={"name": "gated-lib", "package_type": "generic"},
    )
    assert artifact.status_code == 201
    artifact_id = artifact.json()["id"]
    assert (
        client.post(
            f"/api/v1/artifacts/{artifact_id}/versions",
            json={"version": "1", "checksum": {"algorithm": "sha256", "value": SHA}},
        ).status_code
        == 201
    )
    reservation = client.post(
        f"/api/v1/artifacts/{artifact_id}/versions/1/download-reservations",
        json={"requester": "gated_user"},
    )
    assert reservation.status_code == 201
    skill = client.post("/api/v1/skills", json={"name": "门禁技能", "version": "1.0.0"})
    assert skill.status_code == 201

    engine = client.app.state.engine
    with engine.connect() as connection:
        before_accounts = connection.execute(text("SELECT count(*) FROM accounts")).scalar_one()
        before_jobs = connection.execute(text("SELECT count(*) FROM executor_jobs")).scalar_one()

    anon_app = create_app(app_settings(llm_provider="off"))
    with TestClient(anon_app) as anon:
        assert anon.get("/health").status_code == 200
        assert anon.get("/").status_code == 200
        docs = anon.get("/docs")
        assert docs.status_code == 200
        spec = anon.get("/openapi.json")
        assert spec.status_code == 200
        document = spec.json()
        assert document["paths"]["/api/v1/accounts"]["post"]["security"]
        schemes = document["components"]["securitySchemes"].values()
        assert any(item.get("scheme") == "bearer" for item in schemes)
        assert "security" not in document["paths"]["/health"]["get"]

        protected_reads = [
            "/api/v1/accounts",
            f"/api/v1/accounts/{created.json()['id']}",
            "/api/v1/rbac/roles",
            "/api/v1/rbac/permissions",
            "/api/v1/artifacts",
            f"/api/v1/artifacts/{artifact_id}",
            reservation.json()["retrieve_path"],
            "/api/v1/skills",
            f"/api/v1/skills/{skill.json()['id']}",
            "/api/v1/llm/provider",
            "/api/v1/executor",
            "/api/v1/jobs",
            "/api/v1/jobs/00000000-0000-0000-0000-000000000001",
            "/api/v1/jobs/00000000-0000-0000-0000-000000000001/log",
            "/api/v1/git/source",
            "/api/v1/git/repositories",
            "/api/v1/git/commits?repository=ada/demo&ref=main",
        ]
        for path in protected_reads:
            response = anon.get(path)
            assert response.status_code == 401, path
            assert response.json()["detail"] == "需要访问令牌"
            assert response.headers["www-authenticate"] == "Bearer"

        writes = [
            ("/api/v1/accounts", {"username": "intruder", "display_name": "闯入"}),
            ("/api/v1/artifacts", {"name": "intruder-lib", "package_type": "generic"}),
            (
                f"/api/v1/artifacts/{artifact_id}/versions",
                {"version": "9", "checksum": {"algorithm": "sha256", "value": SHA}},
            ),
            (
                f"/api/v1/artifacts/{artifact_id}/versions/1/download-reservations",
                {"requester": "gated_user"},
            ),
            ("/api/v1/skills", {"name": "闯入技能", "version": "1.0.0"}),
            (f"/api/v1/skills/{skill.json()['id']}/submit-review", None),
            (f"/api/v1/skills/{skill.json()['id']}/publish", None),
            ("/api/v1/llm/chat", {"messages": [{"role": "user", "content": "你好"}]}),
            ("/api/v1/jobs", {"marker": "anonblocked"}),
        ]
        for path, payload in writes:
            kwargs = {"json": payload} if payload is not None else {}
            response = anon.post(path, **kwargs)
            assert response.status_code == 401, path

        again = anon.post(
            "/api/v1/accounts",
            json={"username": "intruder", "display_name": "闯入"},
        )
        assert again.status_code == 401
        queried = anon.post(
            f"/api/v1/accounts?access_token={LOCAL_API_TOKEN}",
            json={"username": "query_user", "display_name": "查询"},
        )
        assert queried.status_code == 401
        wrong = anon.post(
            "/api/v1/accounts",
            json={"username": "wrong_user", "display_name": "错误"},
            headers={"Authorization": "Bearer not-the-token"},
        )
        assert wrong.status_code == 401

    with engine.connect() as connection:
        after_accounts = connection.execute(text("SELECT count(*) FROM accounts")).scalar_one()
        after_jobs = connection.execute(text("SELECT count(*) FROM executor_jobs")).scalar_one()
        intruders = connection.execute(
            text("SELECT count(*) FROM accounts WHERE username = 'intruder'")
        ).scalar_one()
        versions = connection.execute(
            text("SELECT count(*) FROM artifact_versions")
        ).scalar_one()
    assert after_accounts == before_accounts
    assert after_jobs == before_jobs == 0
    assert intruders == 0
    assert versions == 1
    listed = client.get("/api/v1/accounts")
    assert [item["username"] for item in listed.json()["items"]] == ["gated_user"]
    assert client.get(reservation.json()["retrieve_path"]).status_code == 200
