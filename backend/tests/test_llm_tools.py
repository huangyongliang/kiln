"""Closed tool loop for 对话.

The model is a scripted in-process httpx.MockTransport. Skill rows and product
builds are real Postgres rows. Git and Jenkins are in-process stubs. These
tests always run. mock and off never enter the loop.
"""

import hashlib
import json
from uuid import UUID

import httpx
import pytest
from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient
from sqlalchemy import text

from kiln.auth import Principal
from kiln.domain.artifacts import BUILD_OWNED_WRITE_DETAIL
from kiln.domain.builds import ProductBuild
from kiln.domain.rbac import ROLE_INDEX, permissions_for
from kiln.errors import GIT_SOURCE_UNCONFIGURED_DETAIL, JENKINS_UNCONFIGURED_DETAIL, UpstreamFailure
from kiln.executor.product import publish
from kiln.llm.provider import ChatMessage, ChatRequest
from kiln.llm.tools import (
    MAX_MODEL_ROUNDS,
    OPENAI_TOOLS,
    PLATFORM_SYSTEM_PROMPT,
    ToolContext,
    run_tool_loop,
)
from kiln.main import create_app
from tests.support import (
    app_settings,
    auth_headers,
    blob_files,
    content_headers,
    publish_skill,
    truncate_domain,
)

_TOOL_NAMES = [
    "list_skills",
    "get_skill",
    "draft_skill",
    "update_skill",
    "build_artifact",
]

LOCAL_PASSWORD = "correct-horse-1"


def test_run_tool_loop_without_allowance_raises() -> None:
    request = ChatRequest(messages=[ChatMessage(role="user", content="ping")])
    with pytest.raises(TypeError, match="allowed"):
        run_tool_loop(request, None, None, None)  # type: ignore[arg-type]
_COMPLETION_KEYS = {"id", "provider", "model", "content", "finish_reason", "actions"}
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
_SKILL = "00000000-0000-4000-8000-0000000000aa"
_BUILD = {
    "skill_id": _SKILL,
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
            assert body["actions"] == [
                {
                    "type": "skill_drafted",
                    "id": json.loads(seen[before + 1]["messages"][-1]["content"])["id"],
                    "name": f"{role}-draft",
                }
            ]
            tool_message = seen[before + 1]["messages"][-1]
            assert tool_message["role"] == "tool"
            drafted = json.loads(tool_message["content"])
            assert drafted["name"] == f"{role}-draft"
            assert drafted["status"] == "draft"
            assert drafted["steps"] == _DRAFT["steps"]
            fetched = session.get(f"/api/v1/skills/{drafted['id']}")
            assert fetched.status_code == 200
            assert fetched.json() == drafted
            assert [item["function"]["name"] for item in seen[before]["tools"]] == _TOOL_NAMES
            assert seen[before]["messages"] == [
                {"role": "system", "content": PLATFORM_SYSTEM_PROMPT},
                {"role": "user", "content": f"起草 {role}-draft"},
            ]
            assert seen[before + 1]["messages"][0] == {
                "role": "system",
                "content": PLATFORM_SYSTEM_PROMPT,
            }
            me = session.get("/api/v1/auth/me").json()
            if role == "machine":
                assert me["account"] is None
                assert drafted["created_by"] is None
            else:
                assert drafted["created_by"] == me["account"]["id"]
        rows = _rows(engine, "SELECT name, status FROM skills ORDER BY name")
        assert rows == [
            ("admin-draft", "draft"),
            ("machine-draft", "draft"),
            ("publisher-draft", "draft"),
        ]
        assert _count(engine, "executor_jobs") == 0
        assert _count(engine, "product_builds") == 0
        assert len(seen) == 6


def test_builder_can_draft_and_update_own_skill(tmp_path) -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        index = len(seen) - 1
        if index == 0:
            return _tool_call("call_draft", "draft_skill", _named(_DRAFT, "builder-draft"))
        if index == 1:
            return _content("已保存草稿。请到 Skill 页提交审核。")
        if index == 2:
            drafted = json.loads(seen[1]["messages"][-1]["content"])
            return _tool_call(
                "call_update",
                "update_skill",
                {
                    **_named(_DRAFT, "builder-draft"),
                    "id": drafted["id"],
                    "description": "改过的说明",
                },
            )
        return _content("已更新草稿。请到 Skill 页提交审核。")

    with _openai_client(handler, tmp_path) as (client, engine):
        builder = _login(client, "ada_builder", ["builder"])
        account_id = builder.get("/api/v1/auth/me").json()["account"]["id"]
        drafted_response = builder.post("/api/v1/llm/chat", json=_chat("起草 builder-draft"))
        assert drafted_response.status_code == 200
        drafted = json.loads(seen[1]["messages"][-1]["content"])
        assert drafted_response.json()["actions"] == [
            {"type": "skill_drafted", "id": drafted["id"], "name": "builder-draft"}
        ]
        assert drafted["created_by"] == account_id
        assert drafted["status"] == "draft"
        updated_response = builder.post("/api/v1/llm/chat", json=_chat("改一下说明"))
        assert updated_response.status_code == 200
        updated = json.loads(seen[3]["messages"][-1]["content"])
        assert updated_response.json()["actions"] == [
            {"type": "skill_updated", "id": drafted["id"], "name": "builder-draft"}
        ]
        assert updated["description"] == "改过的说明"
        assert updated["status"] == "draft"
        assert updated["created_by"] == account_id
        assert updated["review_note"] is None
        stored = builder.get(f"/api/v1/skills/{drafted['id']}")
        assert stored.status_code == 200
        assert stored.json()["description"] == "改过的说明"
        assert _count(engine, "skills") == 1
        assert seen[0]["messages"][0] == {"role": "system", "content": PLATFORM_SYSTEM_PROMPT}
        assert [item["function"]["name"] for item in seen[0]["tools"]] == _TOOL_NAMES


def test_draft_and_update_without_agent_draft_skill_do_not_write(tmp_path) -> None:
    seen: list[dict] = []
    calls = (
        ("draft_skill", _named(_DRAFT, "denied-draft")),
        (
            "update_skill",
            {**_named(_DRAFT, "denied-draft"), "id": "00000000-0000-4000-8000-0000000000bb"},
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        if len(seen) % 2 == 1:
            name, payload = calls[(len(seen) - 1) // 2]
            return _tool_call("call_write", name, payload)
        return _content("没有权限，未写入。")

    with _openai_client(handler, tmp_path) as (client, engine):
        kept = client.post("/api/v1/skills", json=_named(_DRAFT, "kept-draft"))
        assert kept.status_code == 201
        for _name, _payload in calls:
            result = run_tool_loop(
                ChatRequest(messages=[ChatMessage(role="user", content="改一条 skill")]),
                client.app.state.llm,
                Principal(
                    kind="user",
                    permissions=frozenset({"agent.qa", "skill.use", "skill.read", "skill.submit"}),
                ),
                _tool_context(client.app),
                **_unmanaged_allowance(client.app),
            )
            assert result.content == "没有权限，未写入。"
            assert result.actions == []
        tool_messages = [
            item
            for body in seen
            for item in body["messages"]
            if item.get("role") == "tool"
        ]
        assert tool_messages == [
            {"role": "tool", "tool_call_id": "call_write", "content": "没有权限"},
            {"role": "tool", "tool_call_id": "call_write", "content": "没有权限"},
        ]
        assert _rows(engine, "SELECT name, status FROM skills") == [("kept-draft", "draft")]


def test_reader_cannot_draft_or_update_a_skill(tmp_path) -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        if len(seen) == 1:
            return _tool_call("call_draft", "draft_skill", _named(_DRAFT, "reader-draft"))
        if len(seen) == 3:
            return _tool_call(
                "call_update",
                "update_skill",
                {**_named(_DRAFT, "reader-draft"), "id": "00000000-0000-4000-8000-0000000000cc"},
            )
        return _content("没有权限。")

    with _openai_client(handler, tmp_path) as (client, engine):
        kept = client.post("/api/v1/skills", json=_named(_DRAFT, "kept-draft"))
        assert kept.status_code == 201
        reader = _login(client, "ada_reader", ["reader"])
        denied = reader.post("/api/v1/llm/chat", json=_chat("起草"))
        assert denied.status_code == 403
        assert denied.json()["detail"] == "没有权限"
        result = run_tool_loop(
            ChatRequest(messages=[ChatMessage(role="user", content="起草")]),
            client.app.state.llm,
            Principal(kind="user", permissions=permissions_for(["reader"])),
            _tool_context(client.app),
                **_unmanaged_allowance(client.app),
        )
        assert result.actions == []
        assert seen[-1]["messages"][-1]["content"] == "没有权限"
        again = run_tool_loop(
            ChatRequest(messages=[ChatMessage(role="user", content="改稿")]),
            client.app.state.llm,
            Principal(kind="user", permissions=permissions_for(["reader"])),
            _tool_context(client.app),
                **_unmanaged_allowance(client.app),
        )
        assert again.actions == []
        assert seen[-1]["messages"][-1]["content"] == "没有权限"
        assert _rows(engine, "SELECT name FROM skills") == [("kept-draft",)]
        assert "agent.draft_skill" in ROLE_INDEX["builder"].permissions
        assert ROLE_INDEX["builder"].permissions.count("agent.draft_skill") == 1
        assert "agent.draft_skill" in ROLE_INDEX["publisher"].permissions
        assert ROLE_INDEX["publisher"].permissions.count("agent.draft_skill") == 1
        assert "agent.draft_skill" in ROLE_INDEX["admin"].permissions
        assert "agent.draft_skill" not in ROLE_INDEX["reader"].permissions
        assert "skill.publish" not in ROLE_INDEX["builder"].permissions


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
                **_unmanaged_allowance(client.app),
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
                **_unmanaged_allowance(client.app),
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


def test_list_and_get_skill_scopes_stay_in_the_tool_result(tmp_path) -> None:
    seen: list[dict] = []
    calls: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        if len(seen) % 2 == 1:
            name, payload = calls[(len(seen) - 1) // 2]
            return _tool_call("call_read", name, payload)
        return _content("已查询。")

    with _openai_client(handler, tmp_path) as (client, engine):
        builder = _login(client, "ada_builder", ["builder"])
        account_id = builder.get("/api/v1/auth/me").json()["account"]["id"]
        own = builder.post("/api/v1/skills", json=_named(_DRAFT, "mine-draft"))
        assert own.status_code == 201
        other = client.post("/api/v1/skills", json=_named(_DRAFT, "other-draft"))
        assert other.status_code == 201
        published = client.post("/api/v1/skills", json=_named(_DRAFT, "usable-skill"))
        assert published.status_code == 201
        published_id = published.json()["id"]
        assert client.post(f"/api/v1/skills/{published_id}/submit-review").status_code == 200
        assert client.post(f"/api/v1/skills/{published_id}/publish").status_code == 200

        queries = (
            ("list_skills", {"scope": "mine"}),
            ("list_skills", {"scope": "usable"}),
            ("list_skills", {"scope": "mine", "status": "draft"}),
            ("list_skills", {}),
            ("get_skill", {"id": own.json()["id"]}),
            ("get_skill", {"id": "00000000-0000-4000-8000-000000000099"}),
            ("get_skill", {"skill_id": own.json()["id"]}),
        )
        calls.extend(queries)
        for _name, _payload in queries:
            response = builder.post("/api/v1/llm/chat", json=_chat("查询 skill"))
            assert response.status_code == 200
            assert response.json()["actions"] == []

        mine = json.loads(seen[1]["messages"][-1]["content"])
        usable = json.loads(seen[3]["messages"][-1]["content"])
        mine_drafts = json.loads(seen[5]["messages"][-1]["content"])
        catalog = json.loads(seen[7]["messages"][-1]["content"])
        fetched = json.loads(seen[9]["messages"][-1]["content"])
        missing = seen[11]["messages"][-1]["content"]
        bad = seen[13]["messages"][-1]["content"]
        assert mine == builder.get("/api/v1/skills", params={"scope": "mine"}).json()
        assert [item["name"] for item in mine["items"]] == ["mine-draft"]
        assert mine["items"][0]["created_by"] == account_id
        assert usable == builder.get("/api/v1/skills", params={"scope": "usable"}).json()
        assert [item["name"] for item in usable["items"]] == ["usable-skill"]
        assert [item["name"] for item in mine_drafts["items"]] == ["mine-draft"]
        assert {item["name"] for item in catalog["items"]} == {
            "mine-draft",
            "other-draft",
            "usable-skill",
        }
        assert any(item["created_by"] is None for item in catalog["items"])
        assert fetched["id"] == own.json()["id"]
        assert missing == "skill 不存在"
        assert bad.startswith("参数不符合要求")
        calls.append(("list_skills", {"scope": "usable"}))
        denied = run_tool_loop(
            ChatRequest(messages=[ChatMessage(role="user", content="列出可用")]),
            client.app.state.llm,
            Principal(kind="user", permissions=frozenset({"agent.qa", "skill.read"})),
            _tool_context(client.app),
                **_unmanaged_allowance(client.app),
        )
        assert denied.content == "已查询。"
        assert seen[-1]["messages"][-1]["content"] == "没有权限"
        assert "usable-skill" not in seen[-1]["messages"][-1]["content"]
        assert _count(engine, "skills") == 3


def test_update_skill_failures_do_not_enter_actions(tmp_path) -> None:
    seen: list[dict] = []
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        if len(seen) % 2 == 1:
            return _tool_call("call_update", "update_skill", calls[(len(seen) - 1) // 2])
        return _content("没有改成。")

    with _openai_client(handler, tmp_path) as (client, engine):
        builder = _login(client, "ada_builder", ["builder"])
        own = builder.post("/api/v1/skills", json=_named(_DRAFT, "own-draft"))
        assert own.status_code == 201
        foreign = client.post("/api/v1/skills", json=_named(_DRAFT, "foreign-draft"))
        assert foreign.status_code == 201
        own_id = own.json()["id"]
        foreign_id = foreign.json()["id"]
        submitted = builder.post(f"/api/v1/skills/{own_id}/submit-review")
        assert submitted.status_code == 200
        calls.extend(
            (
                {**_named(_DRAFT, "own-draft"), "id": own_id, "description": "审核中不能改"},
                {
                    **_named(_DRAFT, "foreign-draft"),
                    "id": foreign_id,
                    "description": "不能改别人的",
                },
                {"id": own_id, "description": "缺名称"},
            )
        )
        for _payload in calls:
            response = builder.post("/api/v1/llm/chat", json=_chat("改稿"))
            assert response.status_code == 200
            assert response.json()["actions"] == []
            assert response.json()["content"] == "没有改成。"
        assert seen[1]["messages"][-1]["content"] == "只有草稿可以修改"
        assert seen[3]["messages"][-1]["content"] == "没有权限"
        assert seen[5]["messages"][-1]["content"].startswith("参数不符合要求")
        stored_own = builder.get(f"/api/v1/skills/{own_id}").json()
        stored_foreign = client.get(f"/api/v1/skills/{foreign_id}").json()
        assert stored_own["status"] == "in_review"
        assert stored_own["description"] == _DRAFT["description"]
        assert stored_foreign["description"] == _DRAFT["description"]
        assert _count(engine, "skills") == 2


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
            assert response.json()["actions"] == []
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


def test_tool_loop_reuses_the_resolved_model_on_every_round(tmp_path) -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        seen.append(body)
        if len(seen) < 3:
            return _tool_call(f"call_{len(seen)}", "list_skills", {})
        return _content("完成", "chatcmpl-done")

    with _openai_client(
        handler,
        tmp_path,
        llm_models="other-model, demo-model",
    ) as (client, engine):
        response = client.post(
            "/api/v1/llm/chat",
            json={"messages": [{"role": "user", "content": "列出"}], "model": "other-model"},
        )
        assert response.status_code == 200
        assert response.json()["model"] == "other-model"
        assert response.json()["content"] == "完成"
        assert _count(engine, "skills") == 0
    assert len(seen) == 3
    assert [item["model"] for item in seen] == ["other-model", "other-model", "other-model"]
    assert [item["function"]["name"] for item in seen[0]["tools"]] == _TOOL_NAMES
    assert "submit_review" not in _TOOL_NAMES
    assert "publish_skill" not in _TOOL_NAMES
    assert MAX_MODEL_ROUNDS == 4
    assert "不能提交审核" in PLATFORM_SYSTEM_PROMPT
    assert "也不能发布" in PLATFORM_SYSTEM_PROMPT


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
        assert [item["type"] for item in response.json()["actions"]] == ["skill_drafted"] * 4
        assert [item["name"] for item in response.json()["actions"]] == [
            f"cap-draft-{index}" for index in range(4)
        ]
        assert len(seen) == 4
        assert [item["function"]["name"] for item in seen[0]["tools"]] == _TOOL_NAMES
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
            assert body["actions"] == []
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
    bound = {"skill_id": _SKILL}

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
            {
                **_named_artifact(_BUILD, f"{role}-pkg"),
                "skill_id": bound["skill_id"],
            },
            "chatcmpl-tool",
        )

    with _openai_client(handler, tmp_path, **_GIT_SETTINGS) as (client, engine):
        git, jenkins = _attach_build_stubs(client.app)
        bound["skill_id"] = publish_skill(client, "chat-flow", ["group/demo"])["id"]
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
            assert [item["function"]["name"] for item in seen[before]["tools"]] == _TOOL_NAMES
            assert seen[before]["messages"][0] == {
                "role": "system",
                "content": PLATFORM_SYSTEM_PROMPT,
            }
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
            assert built["skill_id"] == bound["skill_id"]
            assert built["skill_name"] == "chat-flow"
            assert body["actions"] == [
                {"type": "build_started", "id": built["id"], "name": built["name"]}
            ]
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
            "package_type, content_stored, skill_id::text FROM product_builds ORDER BY name",
        )
        assert rows == [
            (
                f"{role}-pkg",
                "group/demo",
                "main",
                _SHA,
                "queued",
                "1.0.0",
                "generic",
                False,
                bound["skill_id"],
            )
            for role in ("admin", "builder", "machine", "publisher")
        ]
        assert _count(engine, "artifacts") == 4
        assert _count(engine, "artifact_versions") == 0
        assert _rows(engine, "SELECT name, status FROM skills") == [("chat-flow", "published")]
        assert _count(engine, "skill_steps") == 0
        assert _count(engine, "executor_jobs") == 0
        assert blob_files(client.app.state.blobs.root) == []
        assert len(seen) == 8


def test_build_artifact_requires_both_permission_codes(tmp_path) -> None:
    """No catalog role splits these codes, so the principal is direct.

    The route gate still requires agent.qa. Missing agent.build_artifact,
    executor.run, or skill.use does not write.
    """

    seen: list[dict] = []
    principals = (
        frozenset({"agent.qa", "executor.run", "skill.use"}),
        frozenset({"agent.qa", "agent.build_artifact", "skill.use"}),
        frozenset({"agent.qa", "agent.build_artifact", "executor.run"}),
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
                **_unmanaged_allowance(client.app),
            )
            assert result.content == "没有权限"
            assert result.actions == []
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
        assert len(seen) == 6


def test_build_artifact_skill_gate_stays_in_the_tool_result(tmp_path) -> None:
    seen: list[dict] = []
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        if len(seen) % 2 == 1:
            return _tool_call("call_build", "build_artifact", calls[(len(seen) - 1) // 2])
        return _content("没有开始。")

    with _openai_client(handler, tmp_path, **_GIT_SETTINGS) as (client, engine):
        git, jenkins = _attach_build_stubs(client.app)
        draft = client.post(
            "/api/v1/skills",
            json={
                "name": "still-draft",
                "version": "0.1.0",
                "allowed_repositories": ["group/demo"],
            },
        )
        assert draft.status_code == 201, draft.text
        draft_id = draft.json()["id"]
        narrow = publish_skill(client, "narrow-flow", ["other/repo"])
        cases = (
            ({**_BUILD, "skill_id": _SKILL}, "skill 不存在"),
            ({**_BUILD, "skill_id": draft_id}, "只有已发布的 Skill 可以发起构建"),
            (
                {key: value for key, value in _BUILD.items() if key != "skill_id"},
                "参数不符合要求",
            ),
        )
        for payload, detail in cases:
            calls.append(payload)
            response = client.post("/api/v1/llm/chat", json=_chat("构建 demo-pkg"))
            assert response.status_code == 200
            assert response.json()["actions"] == []
            content = seen[-1]["messages"][-1]["content"]
            assert content == detail or content.startswith(detail)

        submitted = client.post(f"/api/v1/skills/{draft_id}/submit-review")
        assert submitted.status_code == 200, submitted.text
        calls.append({**_BUILD, "skill_id": draft_id})
        reviewing = client.post("/api/v1/llm/chat", json=_chat("再构建"))
        assert reviewing.status_code == 200
        assert reviewing.json()["actions"] == []
        assert seen[-1]["messages"][-1]["content"] == "只有已发布的 Skill 可以发起构建"

        calls.append({**_BUILD, "skill_id": narrow["id"]})
        blocked = client.post("/api/v1/llm/chat", json=_chat("换一个仓库"))
        assert blocked.status_code == 200
        assert blocked.json()["actions"] == []
        assert seen[-1]["messages"][-1]["content"] == "仓库不在该 Skill 的允许范围内"
        assert [item["function"]["name"] for item in seen[0]["tools"]] == _TOOL_NAMES
        assert "submit_review" not in _TOOL_NAMES
        assert "publish_skill" not in _TOOL_NAMES
        assert git.calls == []
        assert jenkins.calls == []
        assert _count(engine, "product_builds") == 0
        assert _count(engine, "artifacts") == 0


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
            "skill_id": _SKILL,
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
        dict(_BUILD),
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
        skill_id = publish_skill(client, "upstream-flow", ["group/demo"])["id"]
        for item in payloads:
            item["skill_id"] = skill_id
        git.resolve_error = UpstreamFailure("代码源无法连接（127.0.0.1:9）")
        response = client.post("/api/v1/llm/chat", json=_chat("构建 demo-pkg"))
        assert response.status_code == 200
        assert response.json()["content"] == "没有开始。"
        assert response.json()["actions"] == []
        assert seen[1]["messages"][-1]["content"] == "代码源无法连接（127.0.0.1:9）"
        direct = client.post("/api/v1/builds", json={**_BUILD, "skill_id": skill_id})
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
        assert response.json()["actions"] == []
        assert seen[3]["messages"][-1]["content"] == "同组内制品名称已存在"
        direct = client.post(
            "/api/v1/builds",
            json={**_named_artifact(_BUILD, "dup-pkg"), "skill_id": skill_id},
        )
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
    bound = {"skill_id": _SKILL}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        if len(seen) == 1:
            return _tool_call(
                "call_build",
                "build_artifact",
                {**_BUILD, "skill_id": bound["skill_id"]},
            )
        return _content("构建没有开始。")

    with _openai_client(handler, tmp_path, **_GIT_SETTINGS) as (client, engine):
        git, jenkins = _attach_build_stubs(client.app)
        bound["skill_id"] = publish_skill(client, "retry-flow", ["group/demo"])["id"]
        jenkins.trigger_error = UpstreamFailure(detail)
        response = client.post("/api/v1/llm/chat", json=_chat("构建 demo-pkg"))
        assert response.status_code == 200
        assert response.json()["content"] == "构建没有开始。"
        assert response.json()["actions"] == []
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
        assert _count(engine, "product_builds") == 1
        assert _count(engine, "artifacts") == 1
        assert _count(engine, "artifact_versions") == 0
        failed = _rows(
            engine,
            "SELECT status, queue_url, build_number, artifact_id::text FROM product_builds",
        )
        assert failed == [("not_built", None, None, failed[0][3])]
        artifact_id = failed[0][3]
        owned = client.get(f"/api/v1/artifacts/{artifact_id}")
        assert owned.status_code == 200, owned.text
        assert owned.json()["build_owned"] is True
        digest = hashlib.sha256(b"manual").hexdigest()
        builder = _login(client, "ada_retry", ["builder"])
        for session in (builder, client):
            registered = session.post(
                f"/api/v1/artifacts/{artifact_id}/versions",
                json={
                    "version": "1.0.0",
                    "checksum": {"algorithm": "sha256", "value": digest},
                },
            )
            assert registered.status_code == 409, registered.text
            assert registered.json()["detail"] == BUILD_OWNED_WRITE_DETAIL
            uploaded = session.put(
                f"/api/v1/artifacts/{artifact_id}/versions/1.0.0/content",
                content=b"manual",
                headers=content_headers(b"manual"),
            )
            assert uploaded.status_code == 409, uploaded.text
            assert uploaded.json()["detail"] == BUILD_OWNED_WRITE_DETAIL
        assert _count(engine, "artifact_versions") == 0
        listed = client.get("/api/v1/builds")
        assert listed.status_code == 200, listed.text
        assert listed.json()["items"][0]["status"] == "not_built"
        assert listed.json()["items"][0]["build_number"] is None
        assert "queue_url" not in listed.json()["items"][0]
        detail_response = client.get(f"/api/v1/builds/{listed.json()['items'][0]['id']}")
        assert detail_response.status_code == 200, detail_response.text
        assert detail_response.json()["status"] == "not_built"
        assert jenkins.polled is False
        direct = client.post(
            "/api/v1/builds",
            json={**_named_artifact(_BUILD, "direct-pkg"), "skill_id": bound["skill_id"]},
        )
        assert direct.status_code == 502
        assert direct.json()["detail"] == detail
        assert _rows(
            engine,
            "SELECT artifact_id::text, status, queue_url, build_number "
            "FROM product_builds ORDER BY created_at, id",
        )[1][1:] == ("not_built", None, None)
        assert _rows(engine, "SELECT name, status FROM skills") == [("retry-flow", "published")]
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


def _unmanaged_allowance(app) -> dict[str, object]:
    """The allowance the loop used to fill in when callers omitted it."""

    settings = app.state.settings
    return {
        "allowed": settings.llm_catalog(),
        "default": settings.llm_request_default(),
        "managed": False,
    }


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
        self.create_error: Exception | None = None
        self.trigger_error: Exception | None = None
        self.on_create: object | None = None
        self.on_trigger: object | None = None
        self.fail_read_progress = True
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
        declare_nexus_parameters: bool = False,
    ) -> None:
        self.calls.append(
            (
                "create_product_job",
                job_name,
                {"ref": ref, "sha": sha, "git_user": git_user},
                clone_url,
            )
        )
        if callable(self.on_create):
            self.on_create(job_name)
        if git_token != "test-token":
            raise AssertionError("git token was not the test value")
        if declare_nexus_parameters:
            raise AssertionError("unset chat build declared nexus parameters")
        if self.create_error is not None:
            raise self.create_error

    def trigger_build(
        self,
        job_name: str,
        *,
        parameters: dict[str, str] | None = None,
    ) -> str:
        self.calls.append(("trigger_build", job_name))
        if callable(self.on_trigger):
            self.on_trigger(job_name)
        if parameters:
            raise AssertionError("unset chat build passed nexus parameters")
        if self.trigger_error is not None:
            raise self.trigger_error
        return "http://127.0.0.1:9/queue/item/1/"

    def delete_job(self, job_name: str) -> None:
        self.calls.append(("delete_job", job_name))

    def read_progress(self, *_args: object, **_kwargs: object) -> tuple[str, int | None]:
        self.polled = True
        if self.fail_read_progress:
            raise AssertionError("read_progress was called")
        return "queued", None

    def download_artifact(self, *_args: object, **_kwargs: object) -> bytes:
        self.downloaded = True
        raise AssertionError("build_artifact downloaded artifact bytes")

    def read_log(self, *_args: object, **_kwargs: object) -> tuple[str, bool]:
        raise AssertionError("build_artifact read the Jenkins log")
