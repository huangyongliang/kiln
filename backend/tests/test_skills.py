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
    assert listed.json()["persistence"] == "memory"
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
