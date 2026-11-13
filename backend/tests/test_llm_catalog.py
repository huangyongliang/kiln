"""Admin model catalog, role assignment, and per-caller chat enforcement."""

import asyncio
import json
import logging
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from kiln.domain.llm_catalog import (
    ASSIGNMENT_DUPLICATE,
    ASSIGNMENT_OUTSIDE,
    DEFAULT_NOT_IN_CATALOG,
    ID_DUPLICATE,
    ID_ILLEGAL,
    ID_TOO_LONG,
    MODEL_NOT_ALLOWED,
    NAME_ILLEGAL,
    NAME_TOO_LONG,
    NO_MODELS_FOR_ACCOUNT,
    ROLE_DEFAULT_NOT_IN_SET,
    ROLE_ID_INVALID,
    ROLE_SET_INVALID,
    SEED_OVER_LIMIT_MESSAGE,
    TOO_MANY,
    CatalogReplace,
    validate_catalog_replace,
)
from kiln.domain.rbac import permissions_for
from kiln.errors import Unprocessable
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
    assert reader_status["models"] == []
    assert reader_status["default_model"] is None
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


_LISTED_ILLEGAL_CODEPOINTS = (
    0x007F,
    0x0085,
    0x00AD,
    0x061C,
    0x200C,
    0x200D,
    0x200E,
    0x200F,
    0x2028,
    0x2029,
    0x3164,
    0xE0001,
)


@pytest.mark.parametrize("codepoint", _LISTED_ILLEGAL_CODEPOINTS)
def test_catalog_rejects_each_listed_illegal_codepoint_in_id_and_display_name(
    codepoint: int,
) -> None:
    sample = chr(codepoint)
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
        bad_id = client.put(
            "/api/v1/llm/catalog",
            json=_replace(
                [{"id": f"model-{sample}a", "display_name": "通义千问"}],
                None,
                {},
            ),
        )
        bad_name = client.put(
            "/api/v1/llm/catalog",
            json=_replace(
                [{"id": "model-a", "display_name": f"通义{sample}千问"}],
                "model-a",
                {},
            ),
        )
        assert bad_id.status_code == 422, bad_id.text
        assert bad_id.json()["detail"] == ID_ILLEGAL
        assert bad_name.status_code == 422, bad_name.text
        assert bad_name.json()["detail"] == NAME_ILLEGAL
        assert client.get("/api/v1/llm/catalog").json() == before


def test_catalog_trim_parity_matches_the_shared_fixture() -> None:
    fixture = json.loads(
        (Path(__file__).resolve().parent / "fixtures" / "catalog_trim_parity.json").read_text(
            encoding="utf-8"
        )
    )
    id_base = fixture["idBase"]
    name_base = fixture["nameBase"]
    for case in fixture["cases"]:
        sample = chr(int(case["cp"], 16))
        for where in ("start", "interior", "end"):
            verdict = case[where]
            _assert_id_trim(sample, id_base, where, verdict)
            _assert_name_trim(sample, name_base, where, verdict)


def _assert_id_trim(sample: str, base: str, where: str, verdict: str) -> None:
    placed = _place_catalog_text(base, sample, where)
    payload = _replace(
        [{"id": placed, "display_name": "通义千问"}],
        placed,
        {"builder": ([placed], placed)},
    )
    try:
        snapshot = validate_catalog_replace(CatalogReplace.model_validate(payload))
    except Unprocessable as exc:
        assert verdict == "reject", (hex(ord(sample)), where, exc.detail)
        assert exc.detail == ID_ILLEGAL
        return
    assert verdict in {"trim", "keep"}, (hex(ord(sample)), where)
    expected = base if verdict == "trim" else placed
    assert snapshot.model_ids() == (expected,)
    assert snapshot.default_model == expected
    builder = next(item for item in snapshot.assignments if item.role_id == "builder")
    assert builder.models == (expected,)
    assert builder.default_model == expected


def _assert_name_trim(sample: str, base: str, where: str, verdict: str) -> None:
    placed = _place_catalog_text(base, sample, where)
    payload = _replace(
        [{"id": "model-a", "display_name": placed}],
        "model-a",
        {},
    )
    try:
        snapshot = validate_catalog_replace(CatalogReplace.model_validate(payload))
    except Unprocessable as exc:
        assert verdict == "reject", (hex(ord(sample)), where, exc.detail)
        assert exc.detail == NAME_ILLEGAL
        return
    assert verdict in {"trim", "keep"}, (hex(ord(sample)), where)
    expected = base if verdict == "trim" else placed
    assert snapshot.models[0].display_name == expected


def _place_catalog_text(base: str, sample: str, where: str) -> str:
    if where == "start":
        return sample + base
    if where == "end":
        return base + sample
    chars = list(base)
    mid = len(chars) // 2
    return "".join(chars[:mid] + [sample] + chars[mid:])


def test_catalog_text_rejects_control_invisible_and_bidi_characters() -> None:
    app = create_app(
        app_settings(
            llm_provider="openai",
            llm_base_url="http://llm.example/v1",
            llm_model="model-a",
            llm_models="model-a",
        )
    )
    samples = ("\x00", "\x07", "\u200b", "\u202e")
    with TestClient(app, headers=auth_headers()) as client:
        truncate_domain(client.app.state.engine)
        before = client.get("/api/v1/llm/catalog").json()
        for sample in samples:
            bad_id = client.put(
                "/api/v1/llm/catalog",
                json=_replace(
                    [{"id": f"model-{sample}a", "display_name": "通义千问"}],
                    None,
                    {},
                ),
            )
            assert bad_id.status_code == 422, bad_id.text
            assert bad_id.json()["detail"] == ID_ILLEGAL
            bad_name = client.put(
                "/api/v1/llm/catalog",
                json=_replace(
                    [{"id": "model-a", "display_name": f"通义{sample}千问"}],
                    "model-a",
                    {},
                ),
            )
            assert bad_name.status_code == 422, bad_name.text
            assert bad_name.json()["detail"] == NAME_ILLEGAL
            assert client.get("/api/v1/llm/catalog").json() == before
        saved = client.put(
            "/api/v1/llm/catalog",
            json=_replace(
                [{"id": "model-a", "display_name": "通义千问"}],
                "model-a",
                {},
            ),
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["models"] == [{"id": "model-a", "display_name": "通义千问"}]


def test_concurrent_first_saves_keep_one_writer_and_never_500() -> None:
    app = create_app(
        app_settings(
            llm_provider="openai",
            llm_base_url="http://llm.example/v1",
            llm_model="model-a",
            llm_models="model-a",
        )
    )
    left = _replace(
        [{"id": "model-left", "display_name": "左"}],
        "model-left",
        {"admin": (["model-left"], "model-left")},
    )
    right = _replace(
        [{"id": "model-right", "display_name": "右"}],
        "model-right",
        {"admin": (["model-right"], "model-right")},
    )
    with TestClient(app, headers=auth_headers()) as client:
        for _ in range(8):
            truncate_domain(client.app.state.engine)
            first, second = asyncio.run(_put_catalog_pair(app, left, right))
            assert first.status_code == 200, first.text
            assert second.status_code == 200, second.text
            final = client.get("/api/v1/llm/catalog").json()
            assert final["source"] == "db"
            assert _catalog_view(final) in (_catalog_view(left), _catalog_view(right))
            assert "duplicate key" not in first.text
            assert "duplicate key" not in second.text


def test_oversized_env_seed_warns_and_unedited_save_stays_422(
    caplog: pytest.LogCaptureFixture,
) -> None:
    listed = ",".join(f"model-{index}" for index in range(32))
    caplog.set_level(logging.WARNING)
    app = create_app(
        app_settings(
            llm_provider="openai",
            llm_base_url="http://llm.example/v1",
            llm_model="default-model",
            llm_models=listed,
        )
    )
    assert any(
        record.levelno == logging.WARNING and "32" in record.getMessage()
        for record in caplog.records
    )
    assert any(SEED_OVER_LIMIT_MESSAGE in record.getMessage() for record in caplog.records)
    with TestClient(app, headers=auth_headers()) as client:
        catalog = client.get("/api/v1/llm/catalog")
        assert catalog.status_code == 200, catalog.text
        body = catalog.json()
        assert len(body["models"]) == 33
        assert body["models"][0]["id"] == "default-model"
        assert body["seed_over_limit"] is True
        assert body["seed_notice"] == SEED_OVER_LIMIT_MESSAGE
        saved = client.put(
            "/api/v1/llm/catalog",
            json={
                "models": body["models"],
                "default_model": body["default_model"],
                "assignments": body["assignments"],
            },
        )
        assert saved.status_code == 422, saved.text
        assert saved.json()["detail"] == TOO_MANY
        again = client.get("/api/v1/llm/catalog").json()
        assert again["source"] == "env"
        assert len(again["models"]) == 33
        assert again["seed_over_limit"] is True


def test_oversized_env_seed_warning_stops_after_the_catalog_is_saved(
    caplog: pytest.LogCaptureFixture,
) -> None:
    listed = ",".join(f"model-{index}" for index in range(32))
    settings = app_settings(
        llm_provider="openai",
        llm_base_url="http://llm.example/v1",
        llm_model="default-model",
        llm_models=listed,
    )
    caplog.set_level(logging.WARNING)
    create_app(settings)
    assert any(SEED_OVER_LIMIT_MESSAGE in record.getMessage() for record in caplog.records)
    app = create_app(settings)
    with TestClient(app, headers=auth_headers()) as client:
        saved = client.put(
            "/api/v1/llm/catalog",
            json=_replace(["model-0"], "model-0", {}),
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["source"] == "db"
        assert saved.json()["seed_over_limit"] is False
    caplog.clear()
    create_app(settings)
    assert not any(SEED_OVER_LIMIT_MESSAGE in record.getMessage() for record in caplog.records)
    assert not any("超过 32" in record.getMessage() for record in caplog.records)


def test_illegal_env_ids_are_listed_at_startup(caplog: pytest.LogCaptureFixture) -> None:
    hidden = "bad\u200bmodel"
    private = "co\ue000model"
    caplog.set_level(logging.WARNING)
    app = create_app(
        app_settings(
            llm_provider="openai",
            llm_base_url="http://llm.example/v1",
            llm_model="plain-model",
            llm_models=f"plain-model,{hidden},{private}",
        )
    )
    warnings = [
        record.getMessage() for record in caplog.records if record.levelno == logging.WARNING
    ]
    listed = next(message for message in warnings if "非法字符" in message)
    assert hidden.encode("unicode_escape").decode("ascii") in listed
    assert private.encode("unicode_escape").decode("ascii") in listed
    with TestClient(app, headers=auth_headers()) as client:
        status = client.get("/api/v1/llm/provider")
    assert status.status_code == 200


def test_illegal_env_ids_are_escaped_in_the_startup_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw = "ab\r\n\u202ecd"
    caplog.set_level(logging.WARNING)
    create_app(
        app_settings(
            llm_provider="openai",
            llm_base_url="http://llm.example/v1",
            llm_model="plain-model",
            llm_models=f"plain-model,{raw}",
        )
    )
    listed = next(
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING and "非法字符" in record.getMessage()
    )
    assert raw.encode("unicode_escape").decode("ascii") in listed
    assert "\r" not in listed
    assert "\n" not in listed
    assert "\u202e" not in listed


def test_reader_without_chat_permission_gets_an_empty_model_list() -> None:
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
            machine = client.get("/api/v1/llm/provider")
            anonymous = TestClient(client.app)
            anon = anonymous.get("/api/v1/llm/provider")
            reader = _login(client, "ada_reader_models", ["reader"])
            unmanaged = reader.get("/api/v1/llm/provider")
            saved = client.put(
                "/api/v1/llm/catalog",
                json=_replace(
                    ["model-a", "model-b"],
                    "model-a",
                    {
                        "reader": (["model-b"], "model-b"),
                        "builder": (["model-a"], "model-a"),
                    },
                ),
            )
            assert saved.status_code == 200, saved.text
            managed = reader.get("/api/v1/llm/provider")
            builder = _login(client, "ada_builder_models", ["builder"])
            builder_status = builder.get("/api/v1/llm/provider")
    assert machine.status_code == 200
    assert machine.json()["models"] == ["model-a", "model-b"]
    assert machine.json()["base_url"] == "http://llm.example/v1"
    assert anon.status_code == 401
    assert unmanaged.status_code == 200
    assert unmanaged.json()["models"] == []
    assert unmanaged.json()["default_model"] is None
    assert unmanaged.json()["base_url"] == "http://llm.example/v1"
    assert unmanaged.json()["provider"] == "openai"
    assert SECRET not in unmanaged.text
    assert managed.status_code == 200
    assert managed.json()["models"] == []
    assert managed.json()["default_model"] is None
    assert managed.json()["base_url"] == "http://llm.example/v1"
    assert builder_status.json()["models"] == ["model-a"]
    assert builder_status.json()["default_model"] == "model-a"
    assert builder_status.json()["base_url"] == "http://llm.example/v1"


def test_invisible_lookalike_ids_are_rejected_and_visible_names_stay() -> None:
    kept = validate_catalog_replace(
        CatalogReplace.model_validate(
            _replace([{"id": "gpt-4o", "display_name": "眼袋 🫩"}], "gpt-4o", {})
        )
    )
    assert kept.model_ids() == ("gpt-4o",)
    assert kept.models[0].display_name == "眼袋 🫩"
    smile = validate_catalog_replace(
        CatalogReplace.model_validate(
            _replace([{"id": "gpt-4o", "display_name": "☺️"}], "gpt-4o", {})
        )
    )
    assert smile.models[0].display_name == "☺️"
    for codepoint in (0xE0080, 0xE0000, 0xE01F0, 0x2065, 0xFFF0):
        dirty = f"gpt-4o{chr(codepoint)}"
        with pytest.raises(Unprocessable) as caught:
            validate_catalog_replace(CatalogReplace.model_validate(_replace([dirty], dirty, {})))
        assert caught.value.detail == ID_ILLEGAL


def test_default_and_role_fields_reject_illegal_characters() -> None:
    samples = ("\ue000", "\ud800", "\u115f", "\u1160", "\uffa0", "\u2800", "\u3164")
    for sample in samples:
        dirty = f"model-{sample}a"
        for payload in (
            _replace(["model-a"], dirty, {}),
            _replace(["model-a"], "model-a", {"builder": ([dirty], None)}),
            _replace(
                ["model-a", "model-b"],
                "model-a",
                {"builder": (["model-a"], dirty)},
            ),
        ):
            with pytest.raises(Unprocessable) as caught:
                validate_catalog_replace(CatalogReplace.model_validate(payload))
            assert caught.value.detail == ID_ILLEGAL
    kept = validate_catalog_replace(
        CatalogReplace.model_validate(
            _replace([{"id": "model-a", "display_name": "通义★"}], "model-a", {})
        )
    )
    assert kept.models[0].display_name == "通义★"


def test_overlapping_first_saves_with_a_missing_state_row_never_500() -> None:
    app = create_app(
        app_settings(
            llm_provider="openai",
            llm_base_url="http://llm.example/v1",
            llm_model="model-a",
            llm_models="model-a",
        )
    )
    left = _replace(
        ["model-shared", "model-left"],
        "model-shared",
        {"admin": (["model-shared", "model-left"], "model-left")},
    )
    right = _replace(
        ["model-shared", "model-right"],
        "model-right",
        {"builder": (["model-shared"], "model-shared")},
    )
    with TestClient(app, headers=auth_headers()) as client:
        for _ in range(8):
            truncate_domain(client.app.state.engine)
            with client.app.state.engine.connect() as connection:
                present = connection.execute(
                    text("SELECT count(*) FROM llm_catalog_state")
                ).scalar_one()
            assert present == 0
            first, second = asyncio.run(_put_catalog_pair(app, left, right))
            assert first.status_code == 200, first.text
            assert second.status_code == 200, second.text
            final = client.get("/api/v1/llm/catalog").json()
            assert final["source"] == "db"
            assert _catalog_view(final) in (_catalog_view(left), _catalog_view(right))
            assert "duplicate key" not in first.text
            assert "duplicate key" not in second.text


def test_catalog_is_forbidden_for_publisher_and_reader_and_anonymous() -> None:
    app = create_app(
        app_settings(
            llm_provider="off",
            llm_model="model-a",
            llm_models="model-a",
        )
    )
    payload = _replace(["model-a"], "model-a", {})
    with TestClient(app, headers=auth_headers()) as client:
        truncate_domain(client.app.state.engine)
        publisher = _login(client, "ada_publisher", ["publisher"])
        reader = _login(client, "ada_reader", ["reader"])
        anonymous = TestClient(client.app)
        for caller in (publisher, reader):
            denied_get = caller.get("/api/v1/llm/catalog")
            denied_put = caller.put("/api/v1/llm/catalog", json=payload)
            assert denied_get.status_code == 403, denied_get.text
            assert denied_get.json() == {"detail": "没有权限（需要：llm.manage）"}
            assert denied_put.status_code == 403, denied_put.text
            assert denied_put.json() == {"detail": "没有权限（需要：llm.manage）"}
        anon_get = anonymous.get("/api/v1/llm/catalog")
        anon_put = anonymous.put("/api/v1/llm/catalog", json=payload)
        assert anon_get.status_code == 401, anon_get.text
        assert anon_get.json()["detail"] == "需要访问令牌"
        assert anon_put.status_code == 401, anon_put.text
        assert anon_put.json()["detail"] == "需要访问令牌"
        assert client.get("/api/v1/llm/catalog").json()["source"] == "env"


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


def _catalog_view(body: dict) -> dict:
    return {
        "models": body["models"],
        "default_model": body["default_model"],
        "assignments": body["assignments"],
    }


async def _put_catalog_pair(app, left: dict, right: dict):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        return await asyncio.gather(
            http.put("/api/v1/llm/catalog", json=left, headers=auth_headers()),
            http.put("/api/v1/llm/catalog", json=right, headers=auth_headers()),
        )


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
