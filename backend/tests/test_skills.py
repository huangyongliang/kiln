from fastapi.testclient import TestClient


def _draft(client: TestClient, name: str = "maven-package") -> dict:
    response = client.post(
        "/api/v1/skills",
        json={
            "name": name,
            "version": "0.1.0",
            "description": "把 Maven 工程打成包",
            "inputs": [{"name": "branch", "description": "分支", "required": True}],
            "toolchain": "jdk17",
            "environment": "maven",
            "allowed_repositories": ["group/demo"],
            "allowed_dependencies": ["maven-central"],
            "steps": [
                {"kind": "checkout", "description": "检出"},
                {"kind": "compile", "description": "编译"},
                {"kind": "package", "description": "打包"},
                {"kind": "archive", "description": "归档"},
            ],
            "output_spec": "jar",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_draft_review_and_publish(client: TestClient) -> None:
    draft = _draft(client)
    assert draft["status"] == "draft"
    assert draft["status_label"] == "草稿"
    assert [step["kind"] for step in draft["steps"]] == [
        "checkout",
        "compile",
        "package",
        "archive",
    ]

    listed = client.get("/api/v1/skills")
    assert listed.json()["persistence"] == "postgres"
    assert listed.json()["items"][0]["id"] == draft["id"]

    early = client.post(f"/api/v1/skills/{draft['id']}/publish")
    assert early.status_code == 409

    reviewed = client.post(f"/api/v1/skills/{draft['id']}/submit-review")
    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "in_review"
    assert reviewed.json()["status_label"] == "审核中"

    published = client.post(f"/api/v1/skills/{draft['id']}/publish")
    assert published.status_code == 200
    assert published.json()["status"] == "published"
    assert published.json()["steps"] == draft["steps"]

    again = client.post(f"/api/v1/skills/{draft['id']}/publish")
    assert again.status_code == 409

    only_published = client.get("/api/v1/skills", params={"status": "published"})
    assert [item["id"] for item in only_published.json()["items"]] == [draft["id"]]


def test_duplicate_skill_version_conflicts(client: TestClient) -> None:
    _draft(client, name="重复技能")
    again = client.post("/api/v1/skills", json={"name": "重复技能", "version": "0.1.0"})
    assert again.status_code == 409


def test_builder_submits_any_draft_and_cannot_publish(client: TestClient) -> None:
    draft = _draft(client, name="submit-gate")
    builder = _login(client, "ada_builder", ["builder"])
    submitted = builder.post(f"/api/v1/skills/{draft['id']}/submit-review")
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "in_review"
    assert submitted.json()["status_label"] == "审核中"

    denied_publish = builder.post(f"/api/v1/skills/{draft['id']}/publish")
    assert denied_publish.status_code == 403
    assert denied_publish.json()["detail"] == "没有权限"
    assert client.get(f"/api/v1/skills/{draft['id']}").json()["status"] == "in_review"

    again = builder.post(f"/api/v1/skills/{draft['id']}/submit-review")
    assert again.status_code == 409
    assert again.json()["detail"] == "只有草稿可以提交审核"

    other = _draft(client, name="reader-draft")
    reader = _login(client, "ada_reader", ["reader"])
    reader_submit = reader.post(f"/api/v1/skills/{other['id']}/submit-review")
    assert reader_submit.status_code == 403
    assert reader_submit.json()["detail"] == "没有权限"
    assert client.get(f"/api/v1/skills/{other['id']}").json()["status"] == "draft"

    publisher = _login(client, "ada_publisher", ["publisher"])
    published = publisher.post(f"/api/v1/skills/{draft['id']}/publish")
    assert published.status_code == 200
    assert published.json()["status"] == "published"


LOCAL_PASSWORD = "correct-horse-1"


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
