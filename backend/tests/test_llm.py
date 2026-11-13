import json
from inspect import signature

import httpx
import pytest
from fastapi.testclient import TestClient

from kiln.domain.llm_catalog import MODEL_NOT_ALLOWED, NO_MODELS_FOR_ACCOUNT
from kiln.errors import ModelDisabled, ModelRequestError
from kiln.llm.provider import (
    ChatMessage,
    ChatRequest,
    DisabledProvider,
    LLMProvider,
    MockProvider,
    OpenAICompatibleProvider,
    chat_completions_url,
)
from kiln.llm.tools import OPENAI_TOOLS, PLATFORM_SYSTEM_PROMPT
from kiln.main import create_app
from tests.support import app_settings, auth_headers

_TOOL_NAMES = [
    "list_skills",
    "get_skill",
    "draft_skill",
    "update_skill",
    "build_artifact",
]


def test_provider_complete_signatures_match() -> None:
    expected = signature(OpenAICompatibleProvider.complete)
    for method in (
        LLMProvider.complete,
        MockProvider.complete,
        DisabledProvider.complete,
    ):
        actual = signature(method)
        assert list(actual.parameters) == list(expected.parameters)
        for name, parameter in expected.parameters.items():
            assert actual.parameters[name].kind is parameter.kind
            assert actual.parameters[name].default == parameter.default


def test_mock_and_off_complete_accept_allowance_and_ignore_it() -> None:
    request = ChatRequest(
        messages=[ChatMessage(role="user", content="ping")],
        model="not-allowed",
    )
    completion = MockProvider().complete(
        request,
        allowed=("only-model",),
        default="only-model",
        managed=True,
    )
    assert completion.provider == "mock"
    assert completion.model == "not-allowed"
    omitted = MockProvider().complete(
        ChatRequest(messages=[ChatMessage(role="user", content="ping")]),
        allowed=("only-model",),
        default="only-model",
        managed=True,
    )
    assert omitted.model == "mock"
    with pytest.raises(ModelDisabled):
        DisabledProvider().complete(
            request,
            allowed=("only-model",),
            default="only-model",
            managed=True,
        )


def test_openai_complete_without_allowance_raises_and_does_not_call_upstream() -> None:
    def explode(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"complete called upstream: {request.url}")

    with httpx.Client(transport=httpx.MockTransport(explode)) as http_client:
        provider = OpenAICompatibleProvider(
            base_url="http://llm.example/v1",
            api_key="",
            client=http_client,
        )
        request = ChatRequest(
            messages=[ChatMessage(role="user", content="ping")],
            model="other-model",
        )
        with pytest.raises(TypeError, match="allowed"):
            provider.complete(request)
        with pytest.raises(ModelRequestError, match="不在允许的模型列表中"):
            provider.complete(
                request,
                allowed=("demo-model",),
                default="demo-model",
                managed=False,
            )


def test_chat_completions_url() -> None:
    assert chat_completions_url("http://llm.example/v1/") == "http://llm.example/v1/chat/completions"
    assert (
        chat_completions_url("http://llm.example/v1/chat/completions")
        == "http://llm.example/v1/chat/completions"
    )


def test_off_mode_does_not_call_network() -> None:
    def explode(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"off mode called the network: {request.url}")

    with httpx.Client(transport=httpx.MockTransport(explode)) as http_client:
        app = create_app(
            app_settings(llm_provider="off", llm_base_url="http://llm.example/v1"),
            http_client=http_client,
        )
        with TestClient(app, headers=auth_headers()) as client:
            status = client.get("/api/v1/llm/provider")
            chat = client.post(
                "/api/v1/llm/chat",
                json={"messages": [{"role": "user", "content": "你好"}]},
            )
    assert status.status_code == 200
    assert status.json()["provider"] == "off"
    assert status.json()["models"] == []
    assert status.json()["api_key_configured"] is False
    assert chat.status_code == 503


def test_mock_mode_does_not_call_network() -> None:
    def explode(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"mock mode called the network: {request.url}")

    with httpx.Client(transport=httpx.MockTransport(explode)) as http_client:
        app = create_app(
            app_settings(llm_provider="mock", llm_api_key="super-secret-value"),
            http_client=http_client,
        )
        with TestClient(app, headers=auth_headers()) as client:
            status = client.get("/api/v1/llm/provider")
            chat = client.post(
                "/api/v1/llm/chat",
                json={"messages": [{"role": "user", "content": "打包"}]},
            )
            schema = client.get("/openapi.json").text
    body = status.json()
    assert body["provider"] == "mock"
    assert body["models"] == []
    assert body["api_key_configured"] is True
    assert "super-secret-value" not in str(body)
    assert "super-secret-value" not in schema
    assert chat.status_code == 200
    assert chat.json()["provider"] == "mock"
    assert chat.json()["actions"] == []
    assert "打包" in chat.json()["content"]


def test_openai_mode_posts_chat_completions() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "pong"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        app = create_app(
            app_settings(
                llm_provider="openai",
                llm_base_url="http://llm.example/v1",
                llm_api_key="test-key",
                llm_model="demo-model",
            ),
            http_client=http_client,
        )
        with TestClient(app, headers=auth_headers()) as client:
            response = client.post(
                "/api/v1/llm/chat",
                json={"messages": [{"role": "user", "content": "ping"}]},
            )
    assert response.status_code == 200
    assert response.json()["provider"] == "openai"
    assert response.json()["content"] == "pong"
    assert response.json()["model"] == "demo-model"
    assert response.json()["actions"] == []
    assert seen["url"] == "http://llm.example/v1/chat/completions"
    assert seen["authorization"] == "Bearer test-key"
    assert seen["body"] == {
        "model": "demo-model",
        "messages": [
            {"role": "system", "content": PLATFORM_SYSTEM_PROMPT},
            {"role": "user", "content": "ping"},
        ],
        "tools": OPENAI_TOOLS,
    }
    assert [item["function"]["name"] for item in OPENAI_TOOLS] == _TOOL_NAMES
    assert "submit_review" not in _TOOL_NAMES
    assert "publish_skill" not in _TOOL_NAMES
    assert "我产出的" in PLATFORM_SYSTEM_PROMPT
    assert "我能用的已发布 Skill" in PLATFORM_SYSTEM_PROMPT
    assert "不能提交审核" in PLATFORM_SYSTEM_PROMPT
    assert "也不能发布" in PLATFORM_SYSTEM_PROMPT
    assert "不会执行" in PLATFORM_SYSTEM_PROMPT
    assert "只有草稿状态可以改稿" in PLATFORM_SYSTEM_PROMPT
    assert "构建必须使用已发布的 Skill" in PLATFORM_SYSTEM_PROMPT


def test_openai_forwards_prior_turns() -> None:
    seen: dict[str, object] = {}
    prior = [
        {"role": "system", "content": "只回答构建问题"},
        {"role": "user", "content": "第一问"},
        {"role": "assistant", "content": "第一答"},
        {"role": "user", "content": "第二问"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-history",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "第二答"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        app = create_app(
            app_settings(
                llm_provider="openai",
                llm_base_url="http://llm.example/v1",
                llm_api_key="test-key",
                llm_model="demo-model",
            ),
            http_client=http_client,
        )
        with TestClient(app, headers=auth_headers()) as client:
            response = client.post("/api/v1/llm/chat", json={"messages": prior})
    assert response.status_code == 200
    assert response.json()["content"] == "第二答"
    assert response.json()["actions"] == []
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["messages"] == [
        {"role": "system", "content": PLATFORM_SYSTEM_PROMPT},
        {"role": "user", "content": "第一问"},
        {"role": "assistant", "content": "第一答"},
        {"role": "user", "content": "第二问"},
    ]
    assert body["messages"][0]["content"].startswith("你协助用户产出可以送审的 Skill。")
    assert all(item["content"] != "只回答构建问题" for item in body["messages"])
    assert [item for item in body["messages"] if item["role"] == "system"] == [body["messages"][0]]


def test_openai_mode_omits_authorization_when_key_is_empty() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={"id": "chatcmpl-local", "choices": [{"message": {"content": "ok"}}]},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        app = create_app(
            app_settings(
                llm_provider="openai",
                llm_base_url="http://127.0.0.1:11434/v1",
                llm_api_key="",
                llm_model="local-model",
            ),
            http_client=http_client,
        )
        with TestClient(app, headers=auth_headers()) as client:
            response = client.post(
                "/api/v1/llm/chat",
                json={"messages": [{"role": "user", "content": "ping"}]},
            )
    assert response.status_code == 200
    assert seen["authorization"] is None


def test_openai_upstream_status_is_not_forwarded() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(503, json={"error": "upstream-secret-body"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        app = create_app(
            app_settings(
                llm_provider="openai",
                llm_base_url="http://llm.example/v1",
                llm_model="demo-model",
            ),
            http_client=http_client,
        )
        with TestClient(app, headers=auth_headers()) as client:
            response = client.post(
                "/api/v1/llm/chat",
                json={"messages": [{"role": "user", "content": "ping"}]},
            )
    assert response.status_code == 502
    assert response.json()["detail"] == "模型端点返回 503"
    assert "upstream-secret-body" not in response.text


def test_openai_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        raise httpx.TimeoutException("timed out")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        app = create_app(
            app_settings(
                llm_provider="openai",
                llm_base_url="http://llm.example/v1",
                llm_model="demo-model",
            ),
            http_client=http_client,
        )
        with TestClient(app, headers=auth_headers()) as client:
            response = client.post(
                "/api/v1/llm/chat",
                json={"messages": [{"role": "user", "content": "ping"}]},
            )
    assert response.status_code == 504


def test_openai_requires_a_model() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(request.url)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        app = create_app(
            app_settings(
                llm_provider="openai",
                llm_base_url="http://llm.example/v1",
            ),
            http_client=http_client,
        )
        with TestClient(app, headers=auth_headers()) as client:
            response = client.post(
                "/api/v1/llm/chat",
                json={"messages": [{"role": "user", "content": "ping"}]},
            )
    assert response.status_code == 400
    assert response.json()["detail"] == NO_MODELS_FOR_ACCOUNT


def test_provider_publishes_the_openai_catalog() -> None:
    app = create_app(
        app_settings(
            llm_provider="openai",
            llm_base_url="http://llm.example/v1",
            llm_model="model-a",
            llm_models="model-b, model-a",
        )
    )
    with TestClient(app, headers=auth_headers()) as client:
        body = client.get("/api/v1/llm/provider").json()
    assert body["provider"] == "openai"
    assert body["base_url"] == "http://llm.example/v1"
    assert body["models"] == ["model-b", "model-a"]
    assert body["default_model"] == "model-a"


def test_provider_prepends_a_default_missing_from_the_catalog() -> None:
    app = create_app(
        app_settings(
            llm_provider="openai",
            llm_base_url="http://llm.example/v1",
            llm_model="model-a",
            llm_models="model-b, model-c",
        )
    )
    with TestClient(app, headers=auth_headers()) as client:
        body = client.get("/api/v1/llm/provider").json()
    assert body["models"] == ["model-a", "model-b", "model-c"]
    assert body["default_model"] == "model-a"


def test_provider_mock_and_off_publish_an_empty_catalog() -> None:
    for provider in ("mock", "off"):
        app = create_app(
            app_settings(
                llm_provider=provider,
                llm_base_url="http://llm.example/v1",
                llm_model="model-a",
                llm_models="model-a, model-b",
            )
        )
        with TestClient(app, headers=auth_headers()) as client:
            body = client.get("/api/v1/llm/provider").json()
        assert body["provider"] == provider
        assert body["models"] == []
        assert body["default_model"] == "model-a"


def test_openai_omitted_model_uses_the_catalog_default() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        seen.append(body["model"])
        return httpx.Response(
            200,
            json={"id": "chatcmpl-default", "choices": [{"message": {"content": "ok"}}]},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        app = create_app(
            app_settings(
                llm_provider="openai",
                llm_base_url="http://llm.example/v1",
                llm_model="",
                llm_models="first-model, second-model",
            ),
            http_client=http_client,
        )
        with TestClient(app, headers=auth_headers()) as client:
            status = client.get("/api/v1/llm/provider")
            blank = client.post(
                "/api/v1/llm/chat",
                json={"messages": [{"role": "user", "content": "ping"}], "model": "  "},
            )
            response = client.post(
                "/api/v1/llm/chat",
                json={"messages": [{"role": "user", "content": "ping"}]},
            )
    assert status.json()["default_model"] is None
    assert status.json()["models"] == ["first-model", "second-model"]
    assert blank.status_code == 200
    assert blank.json()["model"] == "first-model"
    assert response.status_code == 200
    assert response.json()["model"] == "first-model"
    assert seen == ["first-model", "first-model"]


def test_openai_sends_an_in_catalog_model() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={"id": "chatcmpl-picked", "choices": [{"message": {"content": "ok"}}]},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        app = create_app(
            app_settings(
                llm_provider="openai",
                llm_base_url="http://llm.example/v1",
                llm_model="model-a",
                llm_models="model-b, model-a",
            ),
            http_client=http_client,
        )
        with TestClient(app, headers=auth_headers()) as client:
            response = client.post(
                "/api/v1/llm/chat",
                json={
                    "messages": [{"role": "user", "content": "ping"}],
                    "model": "  model-b  ",
                },
            )
    assert response.status_code == 200
    assert response.json()["model"] == "model-b"
    assert seen["body"]["model"] == "model-b"


def test_openai_rejects_a_model_outside_the_catalog() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"out-of-catalog model called upstream: {request.url}")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        app = create_app(
            app_settings(
                llm_provider="openai",
                llm_base_url="http://llm.example/v1",
                llm_model="model-a",
                llm_models="model-a, model-b",
            ),
            http_client=http_client,
        )
        with TestClient(app, headers=auth_headers()) as client:
            response = client.post(
                "/api/v1/llm/chat",
                json={"messages": [{"role": "user", "content": "ping"}], "model": "other-model"},
            )
    assert response.status_code == 400
    assert response.json()["detail"] == "模型不在允许的模型列表中。"


def test_unmanaged_openai_allows_only_the_configured_llm_model() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode())["model"])
        return httpx.Response(
            200,
            json={"id": "chatcmpl-only", "choices": [{"message": {"content": "ok"}}]},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        app = create_app(
            app_settings(
                llm_provider="openai",
                llm_base_url="http://llm.example/v1",
                llm_model="only-model",
                llm_models="",
            ),
            http_client=http_client,
        )
        with TestClient(app, headers=auth_headers()) as client:
            named = client.post(
                "/api/v1/llm/chat",
                json={"messages": [{"role": "user", "content": "ping"}], "model": "only-model"},
            )
            omitted = client.post(
                "/api/v1/llm/chat",
                json={"messages": [{"role": "user", "content": "ping"}]},
            )
            other = client.post(
                "/api/v1/llm/chat",
                json={"messages": [{"role": "user", "content": "ping"}], "model": "other-model"},
            )
    assert named.status_code == 200
    assert named.json()["model"] == "only-model"
    assert omitted.status_code == 200
    assert omitted.json()["model"] == "only-model"
    assert seen == ["only-model", "only-model"]
    assert other.status_code == 400
    assert other.json()["detail"] == MODEL_NOT_ALLOWED


def test_unmanaged_openai_with_nothing_configured_rejects_every_model() -> None:
    def explode(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unconfigured openai called upstream: {request.url}")

    with httpx.Client(transport=httpx.MockTransport(explode)) as http_client:
        app = create_app(
            app_settings(
                llm_provider="openai",
                llm_base_url="http://llm.example/v1",
                llm_model="",
                llm_models="",
            ),
            http_client=http_client,
        )
        with TestClient(app, headers=auth_headers()) as client:
            status = client.get("/api/v1/llm/provider")
            named = client.post(
                "/api/v1/llm/chat",
                json={"messages": [{"role": "user", "content": "ping"}], "model": "script-model"},
            )
            omitted = client.post(
                "/api/v1/llm/chat",
                json={"messages": [{"role": "user", "content": "ping"}]},
            )
    assert status.json()["models"] == []
    assert status.json()["default_model"] is None
    assert named.status_code == 400
    assert named.json()["detail"] == NO_MODELS_FOR_ACCOUNT
    assert omitted.status_code == 400
    assert omitted.json()["detail"] == NO_MODELS_FOR_ACCOUNT


def test_mock_ignores_the_catalog_with_or_without_a_model() -> None:
    def explode(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"mock mode called the network: {request.url}")

    with httpx.Client(transport=httpx.MockTransport(explode)) as http_client:
        app = create_app(
            app_settings(
                llm_provider="mock",
                llm_model="model-a",
                llm_models="model-a",
            ),
            http_client=http_client,
        )
        with TestClient(app, headers=auth_headers()) as client:
            plain = client.post(
                "/api/v1/llm/chat",
                json={"messages": [{"role": "user", "content": "你好"}]},
            )
            chosen = client.post(
                "/api/v1/llm/chat",
                json={"messages": [{"role": "user", "content": "你好"}], "model": "not-in-catalog"},
            )
    assert plain.status_code == 200
    assert plain.json()["model"] == "mock"
    assert plain.json()["provider"] == "mock"
    assert chosen.status_code == 200
    assert chosen.json()["model"] == "not-in-catalog"
    assert "这是模拟回复" in chosen.json()["content"]


def test_off_stays_disabled_when_the_body_names_a_model() -> None:
    def explode(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"off mode called the network: {request.url}")

    with httpx.Client(transport=httpx.MockTransport(explode)) as http_client:
        app = create_app(
            app_settings(
                llm_provider="off",
                llm_model="model-a",
                llm_models="model-a, model-b",
            ),
            http_client=http_client,
        )
        with TestClient(app, headers=auth_headers()) as client:
            response = client.post(
                "/api/v1/llm/chat",
                json={"messages": [{"role": "user", "content": "你好"}], "model": "model-b"},
            )
    assert response.status_code == 503
    assert "模型未启用" in response.json()["detail"]
