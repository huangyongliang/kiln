"""Closed tool loop for 对话.

The model is a scripted in-process httpx.MockTransport. Skill rows and product
builds are real Postgres rows. Git and Jenkins are in-process stubs. These
tests always run. mock and off never enter the loop.
"""

import json
from uuid import UUID

import httpx
from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient
from sqlalchemy import text

from kiln.auth import Principal
from kiln.domain.builds import ProductBuild
from kiln.errors import GIT_SOURCE_UNCONFIGURED_DETAIL, JENKINS_UNCONFIGURED_DETAIL, UpstreamFailure
from kiln.executor.product import publish
from kiln.llm.provider import ChatMessage, ChatRequest
from kiln.llm.tools import MAX_MODEL_ROUNDS, OPENAI_TOOLS, ToolContext, run_tool_loop
from kiln.main import create_app
from tests.support import app_settings, auth_headers, blob_files, truncate_domain

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
_SHA = "a" * 40
_BUILD = {
    "repository": "group/demo",
    "ref": "main",
    "artifact_name": "demo-pkg",
    "version": "1.0.0",
    "package_type": "generic",
}
_GIT_SETTINGS = {
    "git_base_url": "http://127.0.0.1:9",
    "git_clone_base_url": "http://127.0.0.1:9",
    "git_token": "test-token",
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
                "build_artifact",
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
        assert _count(engine, "product_builds") == 0
        assert _count(engine, "artifacts") == 0


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
        assert _count(engine, "product_builds") == 0
        assert _count(engine, "artifacts") == 0


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
            _tool_context(client.app),
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
            _tool_context(client.app),
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
            return _tool_call("call_other", "not_a_tool", {"name": "not-a-skill"})
        return _content("没有这个工具。")

    with _openai_client(handler, tmp_path) as (client, engine):
        response = client.post("/api/v1/llm/chat", json=_chat("做个制品"))
        assert response.status_code == 200
        assert seen[1]["messages"][-1]["content"] == "未知工具"
        assert _count(engine, "skills") == 0
        assert _count(engine, "executor_jobs") == 0
        assert _count(engine, "product_builds") == 0
        assert _count(engine, "artifacts") == 0


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
            "build_artifact",
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


def test_authorized_roles_can_start_a_product_build(tmp_path) -> None:
    roles = ("builder", "publisher", "admin", "machine")
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        assert str(request.url) == "http://llm.example/v1/chat/completions"
        index = len(seen) - 1
        if index % 2 == 1:
            return _content("已发起构建。", "chatcmpl-final")
        role = roles[index // 2]
        return _tool_call(
            "call_build",
            "build_artifact",
            _named_artifact(_BUILD, f"{role}-pkg"),
            "chatcmpl-tool",
        )

    with _openai_client(handler, tmp_path, **_GIT_SETTINGS) as (client, engine):
        git, jenkins = _attach_build_stubs(client.app)
        sessions = {
            "builder": _login(client, "ada_builder", ["builder"]),
            "publisher": _login(client, "ada_publisher", ["publisher"]),
            "admin": _login(client, "ada_admin", ["admin"]),
            "machine": client,
        }
        for role, session in sessions.items():
            before = len(seen)
            response = session.post(
                "/api/v1/llm/chat",
                json=_chat(f"构建 {role}-pkg"),
            )
            assert response.status_code == 200, role
            body = response.json()
            assert set(body) == _COMPLETION_KEYS
            assert "tool_calls" not in body
            assert body["content"] == "已发起构建。"
            assert body["finish_reason"] == "stop"
            assert seen[before]["tools"] == OPENAI_TOOLS
            assert [item["function"]["name"] for item in seen[before]["tools"]] == [
                "list_skills",
                "draft_skill",
                "build_artifact",
            ]
            tool_message = seen[before + 1]["messages"][-1]
            assert tool_message["role"] == "tool"
            assert tool_message["tool_call_id"] == "call_build"
            built = json.loads(tool_message["content"])
            stored = client.app.state.builds.get_build(UUID(built["id"]))
            published = jsonable_encoder(publish(client.app.state.builds, stored))
            assert built == published
            assert set(built) == set(ProductBuild.model_fields)
            assert built["deploys_to_runtime"] is False
            assert built["content_available"] is False
            assert built["status"] == "queued"
            assert built["build_number"] is None
            assert built["name"] == f"{role}-pkg"
            assert "artifact_name" not in built
            assert built["repository"] == "group/demo"
            assert built["ref"] == "main"
            assert built["commit_sha"] == _SHA
            assert built["version"] == "1.0.0"
            assert built["package_type"] == "generic"
        assert git.calls == [
            ("resolve_ref", "group", "demo", "main"),
            ("authenticated_username",),
        ] * 4
        assert len(jenkins.calls) == 8
        for index in range(4):
            created, triggered = jenkins.calls[index * 2], jenkins.calls[index * 2 + 1]
            assert created[0] == "create_product_job"
            assert triggered == ("trigger_build", created[1])
            assert created[1].startswith("kiln-build-")
            assert created[2] == {"ref": "main", "sha": _SHA, "git_user": "kiln"}
            assert "test-token" not in created[3]
        assert jenkins.polled is False
        assert jenkins.downloaded is False
        rows = _rows(
            engine,
            "SELECT name, repository, git_ref, commit_sha, status, version, "
            "package_type, content_stored FROM product_builds ORDER BY name",
        )
        assert rows == [
            (f"{role}-pkg", "group/demo", "main", _SHA, "queued", "1.0.0", "generic", False)
            for role in ("admin", "builder", "machine", "publisher")
        ]
        assert _count(engine, "artifacts") == 4
        assert _count(engine, "artifact_versions") == 0
        assert _count(engine, "skills") == 0
        assert _count(engine, "skill_steps") == 0
        assert _count(engine, "executor_jobs") == 0
        assert blob_files(client.app.state.blobs.root) == []
        assert len(seen) == 8


def test_build_artifact_requires_both_permission_codes(tmp_path) -> None:
    """No catalog role has agent.qa without both codes, so the principal is direct.

    The route gate still requires agent.qa. Missing either tool code does not write.
    """

    seen: list[dict] = []
    principals = (
        frozenset({"agent.qa", "executor.run"}),
        frozenset({"agent.qa", "agent.build_artifact"}),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        if len(seen) % 2 == 1:
            return _tool_call("call_build", "build_artifact", _BUILD)
        return _content("没有权限")

    with _openai_client(handler, tmp_path, **_GIT_SETTINGS) as (client, engine):
        git, jenkins = _attach_build_stubs(client.app)
        for permissions in principals:
            before = len(seen)
            result = run_tool_loop(
                ChatRequest(messages=[ChatMessage(role="user", content="构建 demo-pkg")]),
                client.app.state.llm,
                Principal(kind="user", permissions=permissions),
                _tool_context(client.app),
            )
            assert result.content == "没有权限"
            assert result.provider == "openai"
            assert seen[before + 1]["messages"][-1] == {
                "role": "tool",
                "tool_call_id": "call_build",
                "content": "没有权限",
            }
        assert git.calls == []
        assert jenkins.calls == []
        assert _count(engine, "product_builds") == 0
        assert _count(engine, "artifacts") == 0
        assert len(seen) == 4


def test_invalid_product_build_arguments_write_nothing(tmp_path) -> None:
    payloads = (
        {
            "repository": "group/demo",
            "ref": "main",
            "artifact_name": "bad-pkg",
            "version": "1.0.0",
            "package_type": "generic",
            "unknown": "no",
        },
        {"artifact_name": "bad-pkg"},
        {
            "repository": "noslash",
            "ref": "main",
            "artifact_name": "bad-pkg",
            "version": "1.0.0",
            "package_type": "generic",
        },
        "[]",
        "{",
    )
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        index = len(seen) - 1
        if index % 2 == 1:
            return _content("未创建。")
        return _tool_call("call_build", "build_artifact", payloads[index // 2])

    with _openai_client(handler, tmp_path, **_GIT_SETTINGS) as (client, engine):
        git, jenkins = _attach_build_stubs(client.app)
        for _payload in payloads:
            response = client.post("/api/v1/llm/chat", json=_chat("构建一个无效制品"))
            assert response.status_code == 200
            assert response.json()["content"] == "未创建。"
            assert set(response.json()) == _COMPLETION_KEYS
        tool_contents = [
            item["content"]
            for body in seen
            for item in body["messages"]
            if item.get("role") == "tool"
        ]
        assert len(tool_contents) == len(payloads)
        assert tool_contents[2] == "仓库必须是 owner/name"
        assert all(
            item.startswith("参数不符合要求")
            for index, item in enumerate(tool_contents)
            if index != 2
        )
        assert git.calls == []
        assert jenkins.calls == []
        assert _count(engine, "product_builds") == 0
        assert _count(engine, "artifacts") == 0
        assert _count(engine, "artifact_versions") == 0


def test_unconfigured_jenkins_or_git_source_is_tool_text(tmp_path) -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        if len(seen) % 2 == 1:
            return _tool_call("call_build", "build_artifact", _BUILD)
        return _content("未配置。")

    with _openai_client(handler, tmp_path, **_GIT_SETTINGS) as (client, engine):
        git = _GitStub()
        client.app.state.git_source = git
        response = client.post("/api/v1/llm/chat", json=_chat("构建 demo-pkg"))
        assert response.status_code == 200
        assert response.json()["content"] == "未配置。"
        assert seen[1]["messages"][-1]["content"] == JENKINS_UNCONFIGURED_DETAIL
        direct = client.post("/api/v1/builds", json=_BUILD)
        assert direct.status_code == 503
        assert direct.json()["detail"] == JENKINS_UNCONFIGURED_DETAIL
        assert git.calls == []

        jenkins = _JenkinsStub()
        client.app.state.jenkins = jenkins
        client.app.state.git_source = None
        response = client.post("/api/v1/llm/chat", json=_chat("构建 demo-pkg"))
        assert response.status_code == 200
        assert response.json()["content"] == "未配置。"
        assert seen[3]["messages"][-1]["content"] == GIT_SOURCE_UNCONFIGURED_DETAIL
        direct = client.post("/api/v1/builds", json=_BUILD)
        assert direct.status_code == 503
        assert direct.json()["detail"] == GIT_SOURCE_UNCONFIGURED_DETAIL
        assert jenkins.calls == []
        assert _count(engine, "product_builds") == 0
        assert _count(engine, "artifacts") == 0


def test_upstream_failure_and_conflict_are_tool_text(tmp_path) -> None:
    seen: list[dict] = []
    payloads = (
        _BUILD,
        _named_artifact(_BUILD, "dup-pkg"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        index = len(seen) - 1
        if index % 2 == 1:
            return _content("没有开始。")
        return _tool_call("call_build", "build_artifact", payloads[index // 2])

    with _openai_client(handler, tmp_path, **_GIT_SETTINGS) as (client, engine):
        git, jenkins = _attach_build_stubs(client.app)
        git.resolve_error = UpstreamFailure("代码源无法连接（127.0.0.1:9）")
        response = client.post("/api/v1/llm/chat", json=_chat("构建 demo-pkg"))
        assert response.status_code == 200
        assert response.json()["content"] == "没有开始。"
        assert seen[1]["messages"][-1]["content"] == "代码源无法连接（127.0.0.1:9）"
        direct = client.post("/api/v1/builds", json=_BUILD)
        assert direct.status_code == 502
        assert direct.json()["detail"] == "代码源无法连接（127.0.0.1:9）"
        assert jenkins.calls == []
        assert _count(engine, "product_builds") == 0
        assert _count(engine, "artifacts") == 0

        git.resolve_error = None
        git.calls.clear()
        created = client.post(
            "/api/v1/artifacts",
            json={"name": "dup-pkg", "package_type": "generic"},
        )
        assert created.status_code == 201, created.text
        response = client.post("/api/v1/llm/chat", json=_chat("再构建一次"))
        assert response.status_code == 200
        assert response.json()["content"] == "没有开始。"
        assert seen[3]["messages"][-1]["content"] == "同组内制品名称已存在"
        direct = client.post("/api/v1/builds", json=_named_artifact(_BUILD, "dup-pkg"))
        assert direct.status_code == 409
        assert direct.json()["detail"] == "同组内制品名称已存在"
        assert jenkins.calls == []
        assert [call[0] for call in git.calls] == ["resolve_ref", "authenticated_username"] * 2
        assert _count(engine, "product_builds") == 0
        assert _count(engine, "artifacts") == 1
        assert blob_files(client.app.state.blobs.root) == []


def test_jenkins_failure_after_artifact_create_deletes_the_job(tmp_path) -> None:
    seen: list[dict] = []
    detail = "Jenkins 调用失败（127.0.0.1:9，HTTP 500）"

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        if len(seen) == 1:
            return _tool_call("call_build", "build_artifact", _BUILD)
        return _content("构建没有开始。")

    with _openai_client(handler, tmp_path, **_GIT_SETTINGS) as (client, engine):
        git, jenkins = _attach_build_stubs(client.app)
        jenkins.trigger_error = UpstreamFailure(detail)
        response = client.post("/api/v1/llm/chat", json=_chat("构建 demo-pkg"))
        assert response.status_code == 200
        assert response.json()["content"] == "构建没有开始。"
        assert set(response.json()) == _COMPLETION_KEYS
        assert seen[1]["messages"][-1]["content"] == detail
        assert [call[0] for call in git.calls] == ["resolve_ref", "authenticated_username"]
        assert [call[0] for call in jenkins.calls] == [
            "create_product_job",
            "trigger_build",
            "delete_job",
        ]
        assert jenkins.calls[0][1] == jenkins.calls[2][1]
        assert jenkins.calls[0][1].startswith("kiln-build-")
        assert jenkins.polled is False
        assert jenkins.downloaded is False
        assert _count(engine, "product_builds") == 0
        assert _count(engine, "artifacts") == 1
        assert _count(engine, "artifact_versions") == 0
        assert _count(engine, "skills") == 0
        assert _count(engine, "executor_jobs") == 0
        assert blob_files(client.app.state.blobs.root) == []


class _Running:
    def __init__(self, handler, tmp_path, **overrides) -> None:
        self._http = httpx.Client(transport=httpx.MockTransport(handler))
        self.app = create_app(
            app_settings(
                llm_provider="openai",
                llm_base_url="http://llm.example/v1",
                llm_api_key="test-key",
                llm_model="demo-model",
                kiln_artifact_root=str(tmp_path / "artifacts"),
                **overrides,
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


def _openai_client(handler, tmp_path, **overrides) -> _Running:
    return _Running(handler, tmp_path, **overrides)


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
    allowed = {
        "skills",
        "skill_steps",
        "executor_jobs",
        "product_builds",
        "artifacts",
        "artifact_versions",
    }
    if table not in allowed:
        raise AssertionError(table)
    with engine.connect() as connection:
        return int(connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())


def _rows(engine, statement: str) -> list[tuple]:
    with engine.connect() as connection:
        return [tuple(row) for row in connection.execute(text(statement))]


def _named_artifact(payload: dict, name: str) -> dict:
    body = json.loads(json.dumps(payload))
    body["artifact_name"] = name
    return body


def _tool_context(app) -> ToolContext:
    return ToolContext(
        catalog=app.state.store,
        builds=app.state.builds,
        jenkins=app.state.jenkins,
        git_source=app.state.git_source,
        settings=app.state.settings,
    )


def _attach_build_stubs(app) -> tuple["_GitStub", "_JenkinsStub"]:
    git = _GitStub()
    jenkins = _JenkinsStub()
    app.state.git_source = git
    app.state.jenkins = jenkins
    return git, jenkins


class _Resolved:
    def __init__(self, sha: str) -> None:
        self.sha = sha


class _GitStub:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.resolve_error: Exception | None = None

    def resolve_ref(self, owner: str, name: str, ref: str) -> _Resolved:
        self.calls.append(("resolve_ref", owner, name, ref))
        if self.resolve_error is not None:
            raise self.resolve_error
        return _Resolved(_SHA)

    def authenticated_username(self) -> str:
        self.calls.append(("authenticated_username",))
        return "kiln"


class _JenkinsStub:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.trigger_error: Exception | None = None
        self.polled = False
        self.downloaded = False

    def create_product_job(
        self,
        job_name: str,
        *,
        clone_url: str,
        ref: str,
        sha: str,
        git_user: str,
        git_token: str,
    ) -> None:
        self.calls.append(
            (
                "create_product_job",
                job_name,
                {"ref": ref, "sha": sha, "git_user": git_user},
                clone_url,
            )
        )
        if git_token != "test-token":
            raise AssertionError("git token was not the test value")

    def trigger_build(self, job_name: str) -> str:
        self.calls.append(("trigger_build", job_name))
        if self.trigger_error is not None:
            raise self.trigger_error
        return "http://127.0.0.1:9/queue/item/1/"

    def delete_job(self, job_name: str) -> None:
        self.calls.append(("delete_job", job_name))

    def read_progress(self, *_args: object, **_kwargs: object) -> tuple[str, int | None]:
        self.polled = True
        raise AssertionError("build_artifact polled Jenkins")

    def download_artifact(self, *_args: object, **_kwargs: object) -> bytes:
        self.downloaded = True
        raise AssertionError("build_artifact downloaded artifact bytes")

    def read_log(self, *_args: object, **_kwargs: object) -> tuple[str, bool]:
        raise AssertionError("build_artifact read the Jenkins log")
