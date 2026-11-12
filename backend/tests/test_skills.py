from datetime import datetime
from uuid import uuid4

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


def test_publisher_rejects_back_to_draft_with_reason(client: TestClient) -> None:
    draft = _draft(client, name="reject-gate")
    assert draft["review_note"] is None
    assert draft["rejected_at"] is None
    assert draft["rejected_by"] is None
    listed = client.get("/api/v1/skills").json()["items"]
    assert listed[0]["id"] == draft["id"]
    assert listed[0]["review_note"] is None
    assert listed[0]["rejected_at"] is None
    assert listed[0]["rejected_by"] is None

    skill_id = draft["id"]
    path = f"/api/v1/skills/{skill_id}/reject"
    early = client.post(path, json={"reason": "还没提交"})
    assert early.status_code == 409
    assert early.json()["detail"] == "只有审核中的 skill 可以退回"
    assert client.get(f"/api/v1/skills/{skill_id}").json()["status"] == "draft"

    builder = _login(client, "ada_builder", ["builder"])
    submitted = builder.post(f"/api/v1/skills/{skill_id}/submit-review")
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "in_review"

    denied = builder.post(path, json={"reason": "构建者不能退回"})
    assert denied.status_code == 403
    assert denied.json()["detail"] == "没有权限"
    reader = _login(client, "ada_reader", ["reader"])
    reader_denied = reader.post(path, json={"reason": "只读不能退回"})
    assert reader_denied.status_code == 403
    assert reader_denied.json()["detail"] == "没有权限"
    missing = f"/api/v1/skills/{uuid4()}/reject"
    assert builder.post(missing, json={"reason": "不存在"}).status_code == 403
    assert reader.post(missing, json={"reason": "不存在"}).status_code == 403
    assert client.get(f"/api/v1/skills/{skill_id}").json()["status"] == "in_review"

    publisher = _login(client, "ada_publisher", ["publisher"])
    publisher_id = publisher.get("/api/v1/auth/me").json()["account"]["id"]
    padded = "\n\t" + ("字" * 500) + "  "
    rejected = publisher.post(path, json={"reason": padded})
    assert rejected.status_code == 200, rejected.text
    body = rejected.json()
    note = "字" * 500
    assert body["status"] == "draft"
    assert body["status_label"] == "草稿"
    assert body["review_note"] == note
    assert body["rejected_at"]
    assert body["rejected_by"] == publisher_id
    rejected_at = datetime.fromisoformat(body["rejected_at"])
    assert rejected_at.tzinfo is not None

    fetched = publisher.get(f"/api/v1/skills/{skill_id}")
    assert fetched.status_code == 200
    assert fetched.json()["review_note"] == note
    assert fetched.json()["rejected_at"] == body["rejected_at"]
    assert fetched.json()["rejected_by"] == publisher_id
    listed_again = publisher.get("/api/v1/skills").json()["items"]
    match = next(item for item in listed_again if item["id"] == skill_id)
    assert match["review_note"] == note
    assert match["rejected_at"] == body["rejected_at"]
    assert match["rejected_by"] == publisher_id

    again = publisher.post(path, json={"reason": "已经退回"})
    assert again.status_code == 409
    assert again.json()["detail"] == "只有审核中的 skill 可以退回"
    publish_early = publisher.post(f"/api/v1/skills/{skill_id}/publish")
    assert publish_early.status_code == 409
    assert publish_early.json()["detail"] == "只有审核中的 skill 可以发布"

    resubmitted = builder.post(f"/api/v1/skills/{skill_id}/submit-review")
    assert resubmitted.status_code == 200
    assert resubmitted.json()["status"] == "in_review"
    assert resubmitted.json()["review_note"] == note
    assert resubmitted.json()["rejected_at"] == body["rejected_at"]
    assert resubmitted.json()["rejected_by"] == publisher_id

    admin = _login(client, "ada_admin", ["admin"])
    admin_id = admin.get("/api/v1/auth/me").json()["account"]["id"]
    admin_note = "请补充\n回归测试\t用例"
    overwritten = admin.post(path, json={"reason": f"  {admin_note}  "})
    assert overwritten.status_code == 200, overwritten.text
    assert overwritten.json()["status"] == "draft"
    assert overwritten.json()["review_note"] == admin_note
    assert overwritten.json()["rejected_by"] == admin_id
    assert overwritten.json()["rejected_at"] != body["rejected_at"]

    resubmitted_again = builder.post(f"/api/v1/skills/{skill_id}/submit-review")
    assert resubmitted_again.status_code == 200
    assert resubmitted_again.json()["status"] == "in_review"
    assert resubmitted_again.json()["review_note"] == admin_note
    machine_note = "由机器令牌退回"
    by_machine = client.post(path, json={"reason": f" {machine_note}\n"})
    assert by_machine.status_code == 200, by_machine.text
    assert by_machine.json()["status"] == "draft"
    assert by_machine.json()["review_note"] == machine_note
    assert by_machine.json()["rejected_by"] is None
    assert by_machine.json()["rejected_at"]

    final_submit = builder.post(f"/api/v1/skills/{skill_id}/submit-review")
    assert final_submit.status_code == 200
    assert final_submit.json()["status"] == "in_review"
    assert final_submit.json()["review_note"] == machine_note
    assert final_submit.json()["rejected_by"] is None
    published = publisher.post(f"/api/v1/skills/{skill_id}/publish")
    assert published.status_code == 200
    assert published.json()["status"] == "published"
    assert published.json()["review_note"] == machine_note
    assert published.json()["rejected_at"] == by_machine.json()["rejected_at"]
    assert published.json()["rejected_by"] is None

    published_reject = publisher.post(path, json={"reason": "已发布不能退回"})
    assert published_reject.status_code == 409
    assert published_reject.json()["detail"] == "只有审核中的 skill 可以退回"
    missing_skill = publisher.post(missing, json={"reason": "没有这条 skill"})
    assert missing_skill.status_code == 404
    assert missing_skill.json()["detail"] == "skill 不存在"


def test_reject_reason_must_be_present_and_drafts_start_clear(client: TestClient) -> None:
    draft = _draft(client, name="reject-validation")
    assert draft["review_note"] is None
    assert draft["rejected_at"] is None
    assert draft["rejected_by"] is None
    created = client.get("/api/v1/skills").json()["items"][0]
    assert created["review_note"] is None
    assert created["rejected_at"] is None
    assert created["rejected_by"] is None

    skill_id = draft["id"]
    submitted = client.post(f"/api/v1/skills/{skill_id}/submit-review")
    assert submitted.status_code == 200
    path = f"/api/v1/skills/{skill_id}/reject"
    cases = [
        None,
        {},
        {"reason": ""},
        {"reason": " \n\t "},
        {"reason": "字" * 501},
        {"reason": "可以", "extra": "不行"},
        {"reason": "含有\x00空字符"},
        {"reason": "含有\r回车"},
        {"reason": "含有\x7f删除符"},
    ]
    for payload in cases:
        kwargs = {} if payload is None else {"json": payload}
        response = client.post(path, **kwargs)
        assert response.status_code == 422, payload
    empty = client.post(path, json={"reason": "   "})
    assert empty.status_code == 422
    assert "退回原因不能为空" in empty.text
    illegal = client.post(path, json={"reason": "bad\x00"})
    assert illegal.status_code == 422
    assert "退回原因含有非法字符" in illegal.text
    current = client.get(f"/api/v1/skills/{skill_id}").json()
    assert current["status"] == "in_review"
    assert current["review_note"] is None
    assert current["rejected_at"] is None
    assert current["rejected_by"] is None

    spec = client.get("/openapi.json").json()
    reject = spec["paths"]["/api/v1/skills/{skill_id}/reject"]["post"]
    assert reject["summary"] == "退回草稿"


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
