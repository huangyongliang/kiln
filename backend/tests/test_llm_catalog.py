"""Admin model catalog, role assignment, and per-caller chat enforcement."""

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import text

from kiln.domain.llm_catalog import (
    ASSIGNMENT_DUPLICATE,
    ASSIGNMENT_OUTSIDE,
    DEFAULT_NOT_IN_CATALOG,
    ID_DUPLICATE,
    ID_TOO_LONG,
    MODEL_NOT_ALLOWED,
    NAME_TOO_LONG,
    NO_MODELS_FOR_ACCOUNT,
    ROLE_DEFAULT_NOT_IN_SET,
    ROLE_ID_INVALID,
    ROLE_SET_INVALID,
    TOO_MANY,
)
from kiln.domain.rbac import permissions_for
from kiln.main import create_app
from tests.support import app_settings, auth_headers, truncate_domain

LOCAL_PASSWORD = "correct-horse-1"
SECRET = "llm-key-do-not-echo-7075"
_ROLES = ("reader", "builder", "publisher", "admin")


def test_unmanaged_provider_matches_today_and_catalog_previews_the_seed() -> None:
    def explode(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"provider status called the network: {request.url}")

    with httpx.Client(transport=httpx.MockTransport(explode)) as http_client:
        app = create_app(
            app_settings(
                llm_provider="openai",
                llm_base_url="http://llm.example/v1",
                llm_api_key=SECRET,
                llm_model="model-a",
                llm_models="model-b",
            ),
            http_client=http_client,
        )
        with TestClient(app, headers=auth_headers()) as client:
            truncate_domain(client.app.state.engine)
            provider = client.get("/api/v1/llm/provider")
            catalog = client.get("/api/v1/llm/catalog")
            builder = _login(client, "ada_builder", ["builder"])
            builder_provider = builder.get("/api/v1/llm/provider")
            denied = builder.get("/api/v1/llm/catalog")
            denied_put = builder.put("/api/v1/llm/catalog", json=_replace([], None, {}))
            _assert_secret_is_not_stored(client, SECRET)
    assert provider.status_code == 200
    assert provider.json()["models"] == ["model-a", "model-b"]
    assert provider.json()["default_model"] == "model-a"
    assert provider.json()["base_url"] == "http://llm.example/v1"
    assert provider.json()["api_key_configured"] is True
    assert all(isinstance(item, str) for item in provider.json()["models"])
    assert builder_provider.json() == provider.json()
    body = catalog.json()
    assert body["source"] == "env"
    assert [item["id"] for item in body["models"]] == ["model-a", "model-b"]
    assert body["default_model"] == "model-a"
    assert body["models"][0]["display_name"] is None
    assert "base_url" not in body
    assigned = {item["role_id"]: item for item in body["assignments"]}
    assert [item["role_id"] for item in body["assignments"]] == list(_ROLES)
    assert assigned["reader"]["models"] == []
    assert assigned["reader"]["default_model"] is None
    for role_id in ("builder", "publisher", "admin"):
        assert assigned[role_id]["models"] == ["model-a", "model-b"]
        assert assigned[role_id]["default_model"] == "model-a"
    assert denied.status_code == 403
    assert denied_put.status_code == 403
    assert SECRET not in provider.text
    assert SECRET not in catalog.text


def test_unmanaged_default_model_stays_null_when_llm_model_is_empty() -> None:
    app = create_app(
        app_settings(
            llm_provider="openai",
            llm_base_url="http://llm.example/v1",
            llm_model="",
            llm_models="first-model, second-model",
        )
    )
    with TestClient(app, headers=auth_headers()) as client:
        provider = client.get("/api/v1/llm/provider").json()
        catalog = client.get("/api/v1/llm/catalog").json()
    assert provider["models"] == ["first-model", "second-model"]
    assert provider["default_model"] is None
    assert catalog["source"] == "env"
    assert catalog["default_model"] == "first-model"
    assert [item["id"] for item in catalog["models"]] == ["first-model", "second-model"]


def test_mock_preview_uses_the_seed_while_provider_models_stay_empty() -> None:
    def explode(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"mock mode called the network: {request.url}")

    with httpx.Client(transport=httpx.MockTransport(explode)) as http_client:
        app = create_app(
            app_settings(
                llm_provider="mock",
                llm_api_key=SECRET,
                llm_model="model-a",
                llm_models="model-b, model-c",
            ),
            http_client=http_client,
        )
        with TestClient(app, headers=auth_headers()) as client:
            provider = client.get("/api/v1/llm/provider")
            catalog = client.get("/api/v1/llm/catalog")
            chat = client.post(
                "/api/v1/llm/chat",
                json=_chat("not-in-seed"),
            )
    assert provider.status_code == 200
    assert provider.json()["provider"] == "mock"
    assert provider.json()["models"] == []
    assert provider.json()["default_model"] == "model-a"
    body = catalog.json()
    assert body["source"] == "env"
    assert [item["id"] for item in body["models"]] == ["model-a", "model-b", "model-c"]
    assert body["provider"] == "mock"
    assert "base_url" not in body
    assert SECRET not in provider.text
    assert SECRET not in catalog.text
    assert chat.status_code == 200
    assert chat.json()["model"] == "not-in-seed"


def test_illegal_saves_return_422_and_do_not_write() -> None:
    app = create_app(
        app_settings(
            llm_provider="openai",
            llm_base_url="http://llm.example/v1",
            llm_model="model-a",
            llm_models="model-a",
        )
    )
    with TestClient(app, headers=auth_headers()) as client:
        truncate_domain(client.app.state.engine)
        before = client.get("/api/v1/llm/catalog").json()
        cases = [
            (
                _replace([{"id": "m" * 129, "display_name": None}], "m" * 129, {}),
                ID_TOO_LONG,
            ),
            (
                _replace([f"model-{index}" for index in range(33)], "model-0", {}),
                TOO_MANY,
            ),
            (
                _replace(["model-a", " model-a "], "model-a", {}),
                ID_DUPLICATE,
            ),
            (
                _replace(
                    ["model-a", "model-b"],
                    "model-a",
                    {"builder": (["model-a"], "model-b")},
                ),
                ROLE_DEFAULT_NOT_IN_SET,
            ),
            (
                _replace(
                    ["model-a"],
                    "model-a",
                    {"builder": (["model-z"], None)},
                ),
                ASSIGNMENT_OUTSIDE,
            ),
            (
                _replace(["model-a"], "model-z", {}),
                DEFAULT_NOT_IN_CATALOG,
            ),
            (
                _replace(
                    ["model-a"],
                    "model-a",
                    {"builder": (["model-a", "model-a"], "model-a")},
                ),
                ASSIGNMENT_DUPLICATE,
            ),
            (
                _replace_roles(
                    ["model-a"],
                    "model-a",
                    [{"role_id": "owner", "models": [], "default_model": None}],
                ),
                ROLE_ID_INVALID,
            ),
            (
                _replace_roles(["model-a"], "model-a", []),
                ROLE_SET_INVALID,
            ),
            (
                _replace(
                    [{"id": "model-a", "display_name": "名" * 129}],
                    "model-a",
                    {},
                ),
                NAME_TOO_LONG,
            ),
        ]
        for payload, detail in cases:
            response = client.put("/api/v1/llm/catalog", json=payload)
            assert response.status_code == 422, response.text
            assert response.json()["detail"] == detail
            assert client.get("/api/v1/llm/catalog").json() == before
        exact = client.put(
            "/api/v1/llm/catalog",
            json=_replace(
                [{"id": " " + ("m" * 128) + " ", "display_name": "名" * 128}],
                "m" * 128,
                {"admin": (["m" * 128], "m" * 128)},
            ),
        )
        assert exact.status_code == 200, exact.text
        saved = exact.json()["models"][0]
        assert saved["id"] == "m" * 128
        assert saved["display_name"] == "名" * 128
        assert NAME_TOO_LONG != ID_TOO_LONG
        assert "模型 id" not in NAME_TOO_LONG


def test_put_replaces_catalog_assignments_and_state_in_one_step() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = httpx.Response(200, json=_completion("ok"))
        seen.append(str(request.url))
        return body

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        app = create_app(
            app_settings(
                llm_provider="openai",
                llm_base_url="http://llm.example/v1",
                llm_api_key=SECRET,
                llm_model="model-a",
                llm_models="model-a,model-b",
            ),
            http_client=http_client,
        )
        with TestClient(app, headers=auth_headers()) as client:
            truncate_domain(client.app.state.engine)
            first = client.put(
                "/api/v1/llm/catalog",
                json=_replace(
                    [
                        {"id": "model-a", "display_name": "甲"},
                        {"id": "model-b", "display_name": "乙"},
                    ],
                    "model-a",
                    {
                        "builder": (["model-a", "model-b"], "model-a"),
                        "publisher": (["model-a"], "model-a"),
                        "admin": (["model-a", "model-b"], "model-a"),
                    },
                ),
            )
            assert first.status_code == 200, first.text
            assert first.json()["source"] == "db"
            assert first.headers.get("etag") is None
            second = client.put(
                "/api/v1/llm/catalog",
                json=_replace(
                    [{"id": "model-b", "display_name": "乙"}],
                    "model-b",
                    {
                        "builder": (["model-b"], "model-b"),
                        "publisher": (["model-b"], "model-b"),
                        "admin": (["model-b"], "model-b"),
                    },
                ),
            )
            assert second.status_code == 200, second.text
            current = client.get("/api/v1/llm/catalog").json()
            assert current["source"] == "db"
            assert current["models"] == [{"id": "model-b", "display_name": "乙"}]
            assert current["default_model"] == "model-b"
            assert all("model-a" not in item["models"] for item in current["assignments"])
            dangling = client.put(
                "/api/v1/llm/catalog",
                json=_replace(
                    ["model-b"],
                    "model-b",
                    {"builder": (["model-a"], None)},
                ),
            )
            assert dangling.status_code == 422
            assert dangling.json()["detail"] == ASSIGNMENT_OUTSIDE
            assert client.get("/api/v1/llm/catalog").json() == current
            builder = _login(client, "ada_builder", ["builder"])
            narrowed = builder.get("/api/v1/llm/provider").json()
            assert narrowed["models"] == ["model-b"]
            assert narrowed["default_model"] == "model-b"
            assert "乙" not in builder.get("/api/v1/llm/provider").text
            rejected = builder.post("/api/v1/llm/chat", json=_chat("model-a"))
            assert rejected.status_code == 400
            assert rejected.json()["detail"] == MODEL_NOT_ALLOWED
            assert seen == []
            accepted = builder.post("/api/v1/llm/chat", json=_chat("model-b"))
            machine = client.get("/api/v1/llm/provider").json()
            _assert_secret_is_not_stored(client, SECRET)
    assert accepted.status_code == 200
    assert accepted.json()["model"] == "model-b"
    assert seen == ["http://llm.example/v1/chat/completions"]
    assert machine["models"] == ["model-b"]
    assert SECRET not in first.text
    assert SECRET not in second.text


def test_saved_catalog_is_not_replaced_when_the_environment_changes(tmp_path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion("ok"))

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    app = create_app(
        app_settings(
            llm_provider="openai",
            llm_base_url="http://llm.example/v1",
            llm_model="model-a",
            llm_models="model-a,model-b",
            kiln_artifact_root=str(tmp_path / "first"),
        ),
        http_client=http_client,
    )
    with TestClient(app, headers=auth_headers()) as client:
        truncate_domain(client.app.state.engine)
        saved = client.put(
            "/api/v1/llm/catalog",
            json=_replace(
                ["model-kept"],
                "model-kept",
                {
                    "builder": (["model-kept"], "model-kept"),
                    "publisher": (["model-kept"], "model-kept"),
                    "admin": (["model-kept"], "model-kept"),
                },
            ),
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["source"] == "db"
    http_client.close()

    restarted = httpx.Client(transport=httpx.MockTransport(handler))
    app = create_app(
        app_settings(
            llm_provider="openai",
            llm_base_url="http://llm.example/v1",
            llm_model="model-z",
            llm_models="model-z",
            kiln_artifact_root=str(tmp_path / "second"),
        ),
        http_client=restarted,
    )
    with TestClient(app, headers=auth_headers()) as client:
        catalog = client.get("/api/v1/llm/catalog").json()
        provider = client.get("/api/v1/llm/provider").json()
    restarted.close()
    assert catalog["source"] == "db"
    assert [item["id"] for item in catalog["models"]] == ["model-kept"]
    assert provider["models"] == ["model-kept"]
    assert provider["default_model"] == "model-kept"
    assert "model-z" not in str(catalog)
    assert "model-z" not in str(provider)


def test_empty_managed_catalog_rejects_every_model_for_an_agent_user() -> None:
    def explode(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"empty catalog called upstream: {request.url}")

    with httpx.Client(transport=httpx.MockTransport(explode)) as http_client:
        app = create_app(
            app_settings(
                llm_provider="openai",
                llm_base_url="http://llm.example/v1",
                llm_model="model-a",
                llm_models="model-a",
            ),
            http_client=http_client,
        )
        with TestClient(app, headers=auth_headers()) as client:
            truncate_domain(client.app.state.engine)
            saved = client.put("/api/v1/llm/catalog", json=_replace([], None, {}))
            assert saved.status_code == 200, saved.text
            assert saved.json()["source"] == "db"
            assert saved.json()["models"] == []
            assert saved.json()["default_model"] is None
            builder = _login(client, "ada_empty", ["builder"])
            status = builder.get("/api/v1/llm/provider")
            named = builder.post("/api/v1/llm/chat", json=_chat("model-a"))
            omitted = builder.post("/api/v1/llm/chat", json=_chat(None))
            machine = client.post("/api/v1/llm/chat", json=_chat("model-a"))
    assert status.json()["models"] == []
    assert status.json()["default_model"] is None
    assert named.status_code == 400
    assert named.json()["detail"] == NO_MODELS_FOR_ACCOUNT
    assert omitted.status_code == 400
    assert omitted.json()["detail"] == NO_MODELS_FOR_ACCOUNT
    assert machine.status_code == 400
    assert machine.json()["detail"] == NO_MODELS_FOR_ACCOUNT
    assert "LLM_MODEL" not in named.json()["detail"]


def test_caller_union_and_default_follow_role_order() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = httpx.Response(200, json=_completion("ok"))
        del request
        return payload

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        app = create_app(
            app_settings(
                llm_provider="openai",
                llm_base_url="http://llm.example/v1",
                llm_model="m1",
                llm_models="m1,m2,m3",
            ),
            http_client=http_client,
        )
        with TestClient(app, headers=auth_headers()) as client:
            truncate_domain(client.app.state.engine)
            saved = client.put(
                "/api/v1/llm/catalog",
                json=_replace(
                    ["m1", "m2", "m3"],
                    "m1",
                    {
                        "reader": (["m2"], "m2"),
                        "builder": (["m2", "m3"], "m3"),
                        "publisher": (["m2"], "m2"),
                        "admin": (["m1"], "m1"),
                    },
                ),
            )
            assert saved.status_code == 200, saved.text
            both = _login(client, "ada_both", ["builder", "publisher"])
            builder = _login(client, "ada_one", ["builder"])
            publisher = _login(client, "ada_pub", ["publisher"])
            admin = _login(client, "ada_admin", ["admin"])
            reader = _login(client, "ada_reader", ["reader"])
            mixed = _login(client, "ada_mixed", ["reader", "builder"])
            machine = client.get("/api/v1/llm/provider").json()
            both_status = both.get("/api/v1/llm/provider").json()
            builder_status = builder.get("/api/v1/llm/provider").json()
            publisher_status = publisher.get("/api/v1/llm/provider").json()
            admin_status = admin.get("/api/v1/llm/provider").json()
            reader_status = reader.get("/api/v1/llm/provider").json()
            mixed_status = mixed.get("/api/v1/llm/provider").json()
            sent = both.post(
                "/api/v1/llm/chat",
                json={"messages": [{"role": "user", "content": "ping"}]},
            )
            admin_me = admin.get("/api/v1/auth/me").json()
            builder_me = builder.get("/api/v1/auth/me").json()
    assert machine["models"] == ["m1", "m2", "m3"]
    assert machine["default_model"] == "m1"
    assert both_status["models"] == ["m2", "m3"]
    assert both_status["default_model"] == "m3"
    assert builder_status["models"] == ["m2", "m3"]
    assert builder_status["default_model"] == "m3"
    assert publisher_status["models"] == ["m2"]
    assert publisher_status["default_model"] == "m2"
    assert admin_status["models"] == ["m1"]
    assert admin_status["default_model"] == "m1"
    assert reader_status["models"] == ["m2"]
    assert reader_status["default_model"] == "m2"
    assert mixed_status["models"] == ["m2", "m3"]
    assert mixed_status["default_model"] == "m2"
    assert sent.status_code == 200
    assert sent.json()["model"] == "m3"
    assert "llm.manage" in admin_me["permissions"]
    assert "llm.manage" not in builder_me["permissions"]
    assert set(admin_me["permissions"]) == permissions_for(["admin"])
    assert "llm.manage" not in permissions_for(["builder"])
    assert "llm.manage" not in permissions_for(["publisher"])
    assert "llm.manage" in permissions_for(["admin"])


def test_mock_ignores_a_saved_catalog_and_still_hides_models() -> None:
    def explode(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"mock mode called the network: {request.url}")

    with httpx.Client(transport=httpx.MockTransport(explode)) as http_client:
        app = create_app(
            app_settings(
                llm_provider="mock",
                llm_api_key=SECRET,
                llm_model="model-a",
                llm_models="model-a,model-b",
            ),
            http_client=http_client,
        )
        with TestClient(app, headers=auth_headers()) as client:
            truncate_domain(client.app.state.engine)
            saved = client.put(
                "/api/v1/llm/catalog",
                json=_replace(
                    ["model-a"],
                    "model-a",
                    {"builder": (["model-a"], "model-a")},
                ),
            )
            assert saved.status_code == 200, saved.text
            provider = client.get("/api/v1/llm/provider")
            catalog = client.get("/api/v1/llm/catalog")
            chat = client.post("/api/v1/llm/chat", json=_chat("not-assigned"))
    assert saved.json()["source"] == "db"
    assert provider.json()["models"] == []
    assert provider.json()["default_model"] == "model-a"
    assert [item["id"] for item in catalog.json()["models"]] == ["model-a"]
    assert SECRET not in provider.text
    assert SECRET not in catalog.text
    assert chat.status_code == 200
    assert chat.json()["model"] == "not-assigned"


def _replace(
    models: list[object],
    default: str | None,
    assignments: dict[str, tuple[list[str], str | None]],
) -> dict:
    normalized = []
    for item in models:
        if isinstance(item, str):
            normalized.append({"id": item, "display_name": None})
        else:
            normalized.append(item)
    roles = []
    for role_id in _ROLES:
        chosen, role_default = assignments.get(role_id, ([], None))
        roles.append(
            {"role_id": role_id, "models": list(chosen), "default_model": role_default}
        )
    return {"models": normalized, "default_model": default, "assignments": roles}


def _replace_roles(models: list[str], default: str | None, assignments: list[dict]) -> dict:
    return {
        "models": [{"id": item, "display_name": None} for item in models],
        "default_model": default,
        "assignments": assignments,
    }


def _chat(model: str | None) -> dict:
    body: dict[str, object] = {"messages": [{"role": "user", "content": "ping"}]}
    if model is not None:
        body["model"] = model
    return body


def _completion(content: str) -> dict:
    return {
        "id": "chatcmpl-catalog",
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
        ],
    }


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
    return session


def _assert_secret_is_not_stored(client: TestClient, secret: str) -> None:
    with client.app.state.engine.connect() as connection:
        stored = connection.execute(
            text(
                """
                SELECT coalesce(string_agg(value, ''), '')
                FROM (
                    SELECT source AS value FROM llm_catalog_state
                    UNION ALL
                    SELECT model_id || coalesce(display_name, '') FROM llm_catalog_models
                    UNION ALL
                    SELECT role_id || model_id FROM llm_role_models
                ) AS rows
                """
            )
        ).scalar_one()
    assert secret not in stored
