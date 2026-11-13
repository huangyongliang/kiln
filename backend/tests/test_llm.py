import json

import httpx
from fastapi.testclient import TestClient

from kiln.llm.provider import chat_completions_url
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
