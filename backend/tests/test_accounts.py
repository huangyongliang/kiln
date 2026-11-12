from fastapi.testclient import TestClient


def test_rbac_catalog_is_closed(client: TestClient) -> None:
    permissions = {item["code"] for item in client.get("/api/v1/rbac/permissions").json()["items"]}
    roles = client.get("/api/v1/rbac/roles").json()["items"]
    assert {role["id"] for role in roles} == {"reader", "builder", "publisher", "admin"}
    for role in roles:
        assert set(role["permissions"]) <= permissions
    admin = next(role for role in roles if role["id"] == "admin")
    assert set(admin["permissions"]) == permissions
    assert "agent.draft_skill" in permissions
    assert "artifact.download" in permissions


def test_create_and_read_local_account(client: TestClient) -> None:
    empty = client.get("/api/v1/accounts")
    assert empty.status_code == 200
    assert empty.json() == {"persistence": "postgres", "items": []}

    created = client.post(
        "/api/v1/accounts",
        json={
            "username": "builder_1",
            "display_name": "构建同学",
            "email": "builder@example.com",
            "role_ids": ["builder"],
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "active"
    assert body["source"] == "local"
    assert body["role_ids"] == ["builder"]
    assert "password" not in body
    assert "idp_issuer" not in body
    assert "idp_subject" not in body
    assert set(body) == {
        "id",
        "username",
        "display_name",
        "email",
        "status",
        "source",
        "role_ids",
        "created_at",
    }

    listed = client.get("/api/v1/accounts")
    assert listed.status_code == 200
    payload = listed.json()
    assert payload["persistence"] == "postgres"
    assert payload["items"][0]["id"] == body["id"]
    assert payload["items"][0]["source"] == "local"
    assert payload["items"][0] == body

    fetched = client.get(f"/api/v1/accounts/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == body
    assert fetched.json()["username"] == "builder_1"


def test_duplicate_username_conflicts(client: TestClient) -> None:
    payload = {"username": "reader_1", "display_name": "只读"}
    assert client.post("/api/v1/accounts", json=payload).status_code == 201
    again = client.post("/api/v1/accounts", json=payload)
    assert again.status_code == 409


def test_unknown_role_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/accounts",
        json={"username": "reader_2", "display_name": "只读", "role_ids": ["owner"]},
    )
    assert response.status_code == 422


def test_missing_account_is_404(client: TestClient) -> None:
    response = client.get("/api/v1/accounts/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_display_name_rejects_invisible_controls(client: TestClient) -> None:
    for char in ("\u202e", "\u2066", "\u200b", "\ufeff"):
        response = client.post(
            "/api/v1/accounts",
            json={"username": "bad_disp", "display_name": f"显示{char}名"},
        )
        assert response.status_code == 422, hex(ord(char))
        assert "显示名含有非法字符" in response.text
    allowed = client.post(
        "/api/v1/accounts",
        json={"username": "zwj_name", "display_name": "显示\u200d名"},
    )
    assert allowed.status_code == 201
    assert allowed.json()["display_name"] == "显示\u200d名"
    assert allowed.json()["source"] == "local"
    zwnj = client.post(
        "/api/v1/accounts",
        json={"username": "zwnj_name", "display_name": "显示\u200c名"},
    )
    assert zwnj.status_code == 201
    assert zwnj.json()["display_name"] == "显示\u200c名"
    spec = client.get("/openapi.json").json()
    account = spec["components"]["schemas"]["Account"]["properties"]
    assert account["source"]["enum"] == ["local", "sync"]
    assert "idp_issuer" not in account
    assert "idp_subject" not in account
