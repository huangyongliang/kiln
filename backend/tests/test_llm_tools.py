"""Closed tool loop for drafting a skill from 对话.

The model is a scripted in-process httpx.MockTransport. Skill rows are real
Postgres rows. mock and off never enter the loop.
"""

import json

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import text

from kiln.auth import Principal
from kiln.llm.provider import ChatMessage, ChatRequest
from kiln.llm.tools import MAX_MODEL_ROUNDS, run_tool_loop
from kiln.main import create_app
from tests.support import app_settings, auth_headers, truncate_domain

LOCAL_PASSWORD = "correct-horse-1"
_COMPLETION_KEYS = {"id", "provider", "model", "content", "finish_reason"}
_DRAFT = {
    "name": "chat-draft",
    "version": "0.1.0",
    "description": "从对话保存的草稿",
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
}


def test_publisher_admin_and_machine_can_draft_a_skill(tmp_path) -> None:
    roles = ("publisher", "admin", "machine")
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        assert str(request.url) == "http://llm.example/v1/chat/completions"
        assert request.headers.get("authorization") == "Bearer test-key"
        index = len(seen) - 1
        if index % 2 == 1:
            return _content("已保存草稿。", "chatcmpl-final")
        role = roles[index // 2]
        return _tool_call(
            "call_draft",
            "draft_skill",
            _named(_DRAFT, f"{role}-draft"),
            "chatcmpl-tool",
        )

    with _openai_client(handler, tmp_path) as (client, engine):
        sessions = {
            "publisher": _login(client, "ada_publisher", ["publisher"]),
            "admin": _login(client, "ada_admin", ["admin"]),
            "machine": client,
        }
        for role, session in sessions.items():
            before = len(seen)
            response = session.post(
                "/api/v1/llm/chat",
                json=_chat(f"起草 {role}-draft"),
            )
            assert response.status_code == 200, role
            body = response.json()
            assert set(body) == _COMPLETION_KEYS
            assert body["provider"] == "openai"
            assert body["model"] == "demo-model"
            assert body["content"] == "已保存草稿。"
            assert body["finish_reason"] == "stop"
            assert "tool_calls" not in body
            tool_message = seen[before + 1]["messages"][-1]
            assert tool_message["role"] == "tool"
            drafted = json.loads(tool_message["content"])
            assert drafted["name"] == f"{role}-draft"
            assert drafted["status"] == "draft"
            assert drafted["steps"] == _DRAFT["steps"]
            fetched = session.get(f"/api/v1/skills/{drafted['id']}")
            assert fetched.status_code == 200
            assert fetched.json() == drafted
            assert [item["function"]["name"] for item in seen[before]["tools"]] == [
                "list_skills",
                "draft_skill",
            ]
            assert seen[before]["messages"] == [
                {"role": "user", "content": f"起草 {role}-draft"}
            ]
        rows = _rows(engine, "SELECT name, status FROM skills ORDER BY name")
        assert rows == [
            ("admin-draft", "draft"),
            ("machine-draft", "draft"),
            ("publisher-draft", "draft"),
        ]
        assert _count(engine, "executor_jobs") == 0
        assert _count(engine, "product_builds") == 0
        assert len(seen) == 6


def test_builder_draft_skill_is_denied_without_a_new_row(tmp_path) -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        if len(seen) == 1:
            return _tool_call("call_draft", "draft_skill", _named(_DRAFT, "builder-draft"))
        return _content("没有权限，未创建草稿。")

    with _openai_client(handler, tmp_path) as (client, engine):
        builder = _login(client, "ada_builder", ["builder"])
        response = builder.post("/api/v1/llm/chat", json=_chat("起草 builder-draft"))
        assert response.status_code == 200
        assert response.json()["content"] == "没有权限，未创建草稿。"
        assert _count(engine, "skills") == 0
        tool_messages = [item for item in seen[1]["messages"] if item["role"] == "tool"]
        assert tool_messages == [
            {"role": "tool", "tool_call_id": "call_draft", "content": "没有权限"}
        ]
        assert len(seen) == 2


def test_builder_can_list_skills(tmp_path) -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        if len(seen) == 1:
            return _tool_call("call_list", "list_skills", {})
        return _content("已列出。")

    with _openai_client(handler, tmp_path) as (client, engine):
        created = client.post("/api/v1/skills", json=_named(_DRAFT, "builder-visible"))
        assert created.status_code == 201
        builder = _login(client, "ada_builder", ["builder"])
        response = builder.post("/api/v1/llm/chat", json=_chat("有哪些 skill"))
        assert response.status_code == 200
        listed = json.loads(seen[1]["messages"][-1]["content"])
        assert listed == builder.get("/api/v1/skills").json()
        assert set(listed) == {"persistence", "items"}
        assert [item["name"] for item in listed["items"]] == ["builder-visible"]
        assert _count(engine, "skills") == 1


def test_reader_chat_is_forbidden_and_writes_nothing(tmp_path) -> None:
    def explode(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"reader chat called the model: {request.url}")

    with _openai_client(explode, tmp_path) as (client, engine):
        created = client.post("/api/v1/skills", json=_named(_DRAFT, "kept-skill"))
        assert created.status_code == 201
        reader = _login(client, "ada_reader", ["reader"])
        denied = reader.post("/api/v1/llm/chat", json=_chat("起草一个 skill"))
        assert denied.status_code == 403
        assert denied.json()["detail"] == "没有权限"
        assert _count(engine, "skills") == 1
        assert _rows(engine, "SELECT name, status FROM skills") == [("kept-skill", "draft")]


def test_anonymous_chat_is_unauthorized_and_writes_nothing(tmp_path) -> None:
    def explode(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"anonymous chat called the model: {request.url}")

    with _openai_client(explode, tmp_path) as (client, engine):
        created = client.post("/api/v1/skills", json=_named(_DRAFT, "kept-skill"))
        assert created.status_code == 201
        anon = TestClient(client.app)
        denied = anon.post("/api/v1/llm/chat", json=_chat("起草一个 skill"))
        assert denied.status_code == 401
        assert _count(engine, "skills") == 1
        assert _rows(engine, "SELECT status FROM skills") == [("draft",)]


def test_list_skills_without_skill_read_does_not_leak(tmp_path) -> None:
    """No catalog role has agent.qa without skill.read, so the principal is direct.

    The route gate still requires agent.qa. This checks the tool's own code.
    """

    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        if len(seen) == 1:
            return _tool_call("call_list", "list_skills", {"status": "draft"})
        return _content("完成")

    with _openai_client(handler, tmp_path) as (client, app_engine):
        created = client.post("/api/v1/skills", json=_named(_DRAFT, "hidden-skill"))
        assert created.status_code == 201
        result = run_tool_loop(
            ChatRequest(messages=[ChatMessage(role="user", content="列出 skill")]),
            client.app.state.llm,
            Principal(kind="user", permissions=frozenset({"agent.qa", "skill.use"})),
            client.app.state.store,
        )
        assert result.content == "完成"
        assert seen[1]["messages"][-1] == {
            "role": "tool",
            "tool_call_id": "call_list",
            "content": "没有权限",
        }
        assert "hidden-skill" not in json.dumps(seen, ensure_ascii=False)
        assert _count(app_engine, "skills") == 1


def test_draft_skill_requires_skill_use_as_well_as_agent_draft_skill(tmp_path) -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        if len(seen) == 1:
            return _tool_call("call_draft", "draft_skill", _named(_DRAFT, "missing-use"))
        return _content("没有权限")

    with _openai_client(handler, tmp_path) as (client, engine):
        result = run_tool_loop(
            ChatRequest(messages=[ChatMessage(role="user", content="起草 missing-use")]),
            client.app.state.llm,
            Principal(
                kind="user",
                permissions=frozenset({"agent.qa", "agent.draft_skill"}),
            ),
            client.app.state.store,
        )
        assert result.content == "没有权限"
        assert seen[1]["messages"][-1]["content"] == "没有权限"
        assert _count(engine, "skills") == 0


def test_list_skills_matches_get_skills_filter(tmp_path) -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        if len(seen) == 1:
            return _tool_call("call_list", "list_skills", {"status": "draft"})
        return _content("已列出草稿。")

    with _openai_client(handler, tmp_path) as (client, _engine):
        draft = client.post("/api/v1/skills", json=_named(_DRAFT, "listed-draft"))
        review = client.post("/api/v1/skills", json=_named(_DRAFT, "listed-review"))
        assert draft.status_code == 201
        assert review.status_code == 201
        submitted = client.post(f"/api/v1/skills/{review.json()['id']}/submit-review")
        assert submitted.status_code == 200
        response = client.post("/api/v1/llm/chat", json=_chat("列出草稿"))
        assert response.status_code == 200
        listed = json.loads(seen[1]["messages"][-1]["content"])
        assert listed == client.get("/api/v1/skills", params={"status": "draft"}).json()
        assert set(listed) == {"persistence", "items"}
        assert [item["name"] for item in listed["items"]] == ["listed-draft"]
        assert "listed-review" not in seen[1]["messages"][-1]["content"]


def test_list_skills_rejects_unknown_fields_without_listing(tmp_path) -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        if len(seen) == 1:
            return _tool_call(
                "call_list",
                "list_skills",
                {"status": "draft", "include": "hidden-skill"},
            )
        return _content("无法列出。")

    with _openai_client(handler, tmp_path) as (client, engine):
        created = client.post("/api/v1/skills", json=_named(_DRAFT, "hidden-skill"))
        assert created.status_code == 201
        response = client.post("/api/v1/llm/chat", json=_chat("列出"))
        assert response.status_code == 200
        content = seen[1]["messages"][-1]["content"]
        assert content.startswith("参数不符合要求")
        assert "hidden-skill" not in content
        assert _count(engine, "skills") == 1


def test_invalid_skill_create_writes_nothing(tmp_path) -> None:
    payloads = (
        {"name": "bad-draft", "steps": [{"kind": "shell", "description": "nope"}]},
        "[]",
        "{",
    )
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        index = len(seen) - 1
        if index % 2 == 1:
            return _content("未创建。")
        return _tool_call("call_draft", "draft_skill", payloads[index // 2])

    with _openai_client(handler, tmp_path) as (client, engine):
        for _payload in payloads:
            response = client.post("/api/v1/llm/chat", json=_chat("起草一个无效 skill"))
            assert response.status_code == 200
            assert response.json()["content"] == "未创建。"
        tool_contents = [
            item["content"]
            for body in seen
            for item in body["messages"]
            if item.get("role") == "tool"
        ]
        assert len(tool_contents) == 3
        assert all(item.startswith("参数不符合要求") for item in tool_contents)
        assert _count(engine, "skills") == 0
        assert _count(engine, "skill_steps") == 0


def test_unknown_tool_is_not_executed(tmp_path) -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        if len(seen) == 1:
            return _tool_call("call_other", "build_artifact", {"name": "not-a-skill"})
        return _content("没有这个工具。")

    with _openai_client(handler, tmp_path) as (client, engine):
        response = client.post("/api/v1/llm/chat", json=_chat("做个制品"))
        assert response.status_code == 200
        assert seen[1]["messages"][-1]["content"] == "未知工具"
        assert _count(engine, "skills") == 0
        assert _count(engine, "executor_jobs") == 0
        assert _count(engine, "product_builds") == 0


def test_tool_loop_stops_at_four_model_rounds(tmp_path) -> None:
    assert MAX_MODEL_ROUNDS == 4
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if len(seen) >= MAX_MODEL_ROUNDS:
            raise AssertionError("model round past the cap")
        index = len(seen)
        seen.append(json.loads(request.content.decode()))
        return _tool_call(
            f"call_{index}",
            "draft_skill",
            {"name": f"cap-draft-{index}", "version": "0.1.0", "steps": [
                {"kind": "package", "description": "打包"}
            ]},
            f"chatcmpl-{index}",
        )

    with _openai_client(handler, tmp_path) as (client, engine):
        response = client.post("/api/v1/llm/chat", json=_chat("连续起草"))
        assert response.status_code == 200
        assert response.json()["content"] == "已达到对话工具轮次上限。"
        assert response.json()["finish_reason"] == "length"
        assert set(response.json()) == _COMPLETION_KEYS
        assert len(seen) == 4
        assert [item["function"]["name"] for item in seen[0]["tools"]] == [
            "list_skills",
            "draft_skill",
        ]
        rows = _rows(engine, "SELECT name, status FROM skills ORDER BY name")
        assert rows == [(f"cap-draft-{index}", "draft") for index in range(4)]
        kinds = _rows(
            engine,
            "SELECT skills.name, skill_steps.kind FROM skills "
            "JOIN skill_steps ON skill_steps.skill_id = skills.id "
            "ORDER BY skills.name, skill_steps.position",
        )
        assert kinds == [(f"cap-draft-{index}", "package") for index in range(4)]
        assert _count(engine, "executor_jobs") == 0
        assert _count(engine, "product_builds") == 0


def test_client_cannot_supply_tools_or_tool_roles(tmp_path) -> None:
    def explode(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"rejected chat called the model: {request.url}")

    with _openai_client(explode, tmp_path) as (client, engine):
        with_tools = client.post(
            "/api/v1/llm/chat",
            json={
                "messages": [{"role": "user", "content": "ping"}],
                "tools": [{"type": "function", "function": {"name": "draft_skill"}}],
            },
        )
        with_role = client.post(
            "/api/v1/llm/chat",
            json={"messages": [{"role": "tool", "content": "结果", "tool_call_id": "call_1"}]},
        )
        assert with_tools.status_code == 422
        assert with_role.status_code == 422
        assert _count(engine, "skills") == 0


def test_public_chat_schema_has_no_tool_trace(tmp_path) -> None:
    app = create_app(
        app_settings(
            llm_provider="off",
            kiln_artifact_root=str(tmp_path / "artifacts"),
        )
    )
    with TestClient(app, headers=auth_headers()) as client:
        schema = client.get("/openapi.json").json()["components"]["schemas"]
    assert set(schema["ChatRequest"]["properties"]) == {"messages", "model"}
    assert schema["ChatMessage"]["properties"]["role"]["enum"] == [
        "system",
        "user",
        "assistant",
    ]
    assert set(schema["ChatCompletion"]["properties"]) == _COMPLETION_KEYS
    assert "tool_calls" not in schema["ChatCompletion"]["properties"]


def test_mock_does_not_invent_or_execute_tools(tmp_path) -> None:
    def explode(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"mock mode called the network: {request.url}")

    with httpx.Client(transport=httpx.MockTransport(explode)) as http_client:
        app = create_app(
            app_settings(
                llm_provider="mock",
                llm_base_url="http://llm.example/v1",
                kiln_artifact_root=str(tmp_path / "artifacts"),
            ),
            http_client=http_client,
        )
        with TestClient(app, headers=auth_headers()) as client:
            truncate_domain(app.state.engine)
            response = client.post(
                "/api/v1/llm/chat",
                json=_chat("请调用 draft_skill 创建技能"),
            )
            assert response.status_code == 200
            body = response.json()
            assert set(body) == _COMPLETION_KEYS
            assert body["provider"] == "mock"
            assert "draft_skill" in body["content"]
            assert "这是模拟回复，未调用外部模型。" in body["content"]
            assert _count(app.state.engine, "skills") == 0


def test_off_does_not_call_network_or_write(tmp_path) -> None:
    def explode(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"off mode called the network: {request.url}")

    with httpx.Client(transport=httpx.MockTransport(explode)) as http_client:
        app = create_app(
            app_settings(
                llm_provider="off",
                llm_base_url="http://llm.example/v1",
                kiln_artifact_root=str(tmp_path / "artifacts"),
            ),
            http_client=http_client,
        )
        with TestClient(app, headers=auth_headers()) as client:
            truncate_domain(app.state.engine)
            response = client.post("/api/v1/llm/chat", json=_chat("起草"))
            assert response.status_code == 503
            assert _count(app.state.engine, "skills") == 0


class _Running:
    def __init__(self, handler, tmp_path) -> None:
        self._http = httpx.Client(transport=httpx.MockTransport(handler))
        self.app = create_app(
            app_settings(
                llm_provider="openai",
                llm_base_url="http://llm.example/v1",
                llm_api_key="test-key",
                llm_model="demo-model",
                kiln_artifact_root=str(tmp_path / "artifacts"),
            ),
            http_client=self._http,
        )
        self._client = TestClient(self.app, headers=auth_headers())

    def __enter__(self):
        self._client.__enter__()
        truncate_domain(self.app.state.engine)
        return self._client, self.app.state.engine

    def __exit__(self, exc_type, exc, tb) -> None:
        self._client.__exit__(exc_type, exc, tb)
        self._http.close()


def _openai_client(handler, tmp_path) -> _Running:
    return _Running(handler, tmp_path)


def _chat(content: str) -> dict:
    return {"messages": [{"role": "user", "content": content}]}


def _named(payload: dict, name: str) -> dict:
    body = json.loads(json.dumps(payload))
    body["name"] = name
    return body


def _tool_call(call_id: str, name: str, arguments: object, completion_id: str = "chatcmpl-tool"):
    if not isinstance(arguments, str):
        argument_text = json.dumps(arguments, ensure_ascii=False)
    else:
        argument_text = arguments
    return httpx.Response(
        200,
        json={
            "id": completion_id,
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": call_id,
                                "type": "function",
                                "function": {"name": name, "arguments": argument_text},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        },
    )


def _content(content: str, completion_id: str = "chatcmpl-final") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": completion_id,
            "choices": [
                {
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
        },
    )


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


def _count(engine, table: str) -> int:
    if table not in {"skills", "skill_steps", "executor_jobs", "product_builds"}:
        raise AssertionError(table)
    with engine.connect() as connection:
        return int(connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())


def _rows(engine, statement: str) -> list[tuple]:
    with engine.connect() as connection:
        return [tuple(row) for row in connection.execute(text(statement))]
