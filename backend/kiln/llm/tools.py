"""Closed tool registry for 对话.

The client cannot supply tools. The chat route runs this loop only for the
OpenAI-compatible provider and attaches the schema itself. ``mock`` and ``off``
never enter the loop, so they neither invent nor execute tools.

A caller missing a tool's permission codes does not write. The tool result is
「没有权限」, and the loop continues until the model returns content or the
round cap is reached. The cap is a code constant, not an environment variable.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, ValidationError

from kiln.auth import Principal
from kiln.db.store import PostgresStore
from kiln.domain.rbac import ALL_PERMISSIONS
from kiln.domain.skills import SkillCreate, SkillList, SkillStatus
from kiln.errors import Conflict, ModelUpstreamError
from kiln.llm.provider import (
    ChatCompletion,
    ChatRequest,
    ModelTurn,
    OpenAICompatibleProvider,
)

# Model rounds, not an environment variable. One round is one chat/completions call.
MAX_MODEL_ROUNDS = 4

TOOL_DENIED = "没有权限"
ROUND_CAP_CONTENT = "已达到对话工具轮次上限。"

_Handler = Callable[[object, PostgresStore], str]


class ListSkillsArguments(BaseModel):
    """Optional status filter. Same values as GET /skills. No other fields."""

    model_config = ConfigDict(extra="forbid")

    status: SkillStatus | None = None


@dataclass(frozen=True)
class _Tool:
    name: str
    description: str
    permissions: frozenset[str]
    parameters: type[BaseModel]
    handler: _Handler


def _json(model: BaseModel) -> str:
    return json.dumps(jsonable_encoder(model), ensure_ascii=False, separators=(",", ":"))


def _list_skills(payload: object, store: PostgresStore) -> str:
    parsed = ListSkillsArguments.model_validate(payload)
    listed = SkillList(items=store.list_skills(parsed.status))
    return _json(listed)


def _draft_skill(payload: object, store: PostgresStore) -> str:
    created = store.create_skill(SkillCreate.model_validate(payload))
    return _json(created)


_TOOLS: tuple[_Tool, ...] = (
    _Tool(
        name="list_skills",
        description=(
            "列出 skill。可按 status 筛选 draft、in_review 或 published。"
            "结果与 GET /skills 相同，不附加字段。"
        ),
        permissions=frozenset({"skill.read"}),
        parameters=ListSkillsArguments,
        handler=_list_skills,
    ),
    _Tool(
        name="draft_skill",
        description=(
            "创建一条 skill 草稿。参数与创建草稿相同，包含名称、版本、输入、步骤和范围。"
            "只保存草稿，不提交审核，不发布，不执行步骤。"
        ),
        permissions=frozenset({"agent.draft_skill", "skill.use"}),
        parameters=SkillCreate,
        handler=_draft_skill,
    ),
)

_TOOLS_BY_NAME = {item.name: item for item in _TOOLS}


def _parameters(model: type[BaseModel]) -> dict[str, object]:
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})
    inlined = _inline(schema, defs)
    if not isinstance(inlined, dict):
        raise RuntimeError("tool parameters must be an object schema")
    return inlined


def _inline(node: object, defs: dict[str, object]) -> object:
    if isinstance(node, dict):
        if "$ref" in node:
            ref = node["$ref"]
            if not isinstance(ref, str):
                raise RuntimeError("tool schema ref must be a string")
            name = ref.rsplit("/", 1)[-1]
            resolved = _inline(defs[name], defs)
            extras = {
                key: _inline(value, defs)
                for key, value in node.items()
                if key not in {"$ref", "title"}
            }
            if extras and isinstance(resolved, dict):
                merged = dict(resolved)
                merged.update(extras)
                return merged
            return resolved
        return {
            key: _inline(value, defs)
            for key, value in node.items()
            if key != "title"
        }
    if isinstance(node, list):
        return [_inline(item, defs) for item in node]
    return node


OPENAI_TOOLS: list[dict[str, object]] = [
    {
        "type": "function",
        "function": {
            "name": item.name,
            "description": item.description,
            "parameters": _parameters(item.parameters),
        },
    }
    for item in _TOOLS
]


def run_tool_loop(
    request: ChatRequest,
    provider: OpenAICompatibleProvider,
    principal: Principal,
    store: PostgresStore,
) -> ChatCompletion:
    """Call the model at most MAX_MODEL_ROUNDS times, executing only known tools."""

    messages: list[dict[str, object]] = [
        {"role": item.role, "content": item.content} for item in request.messages
    ]
    turn: ModelTurn | None = None
    for _ in range(MAX_MODEL_ROUNDS):
        turn = provider.complete_turn(
            requested_model=request.model,
            messages=messages,
            tools=OPENAI_TOOLS,
        )
        if not turn.tool_calls:
            if not turn.content:
                raise ModelUpstreamError("模型端点返回了无法识别的响应")
            return ChatCompletion(
                id=turn.id,
                provider="openai",
                model=turn.model,
                content=turn.content,
                finish_reason=turn.finish_reason,
            )
        messages.append(_assistant_tool_message(turn))
        for call in turn.tool_calls:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": execute_tool(call.name, call.arguments, principal, store),
                }
            )
    if turn is None:
        raise ModelUpstreamError("模型端点返回了无法识别的响应")
    return ChatCompletion(
        id=turn.id,
        provider="openai",
        model=turn.model,
        content=ROUND_CAP_CONTENT,
        finish_reason="length",
    )


def execute_tool(
    name: str,
    arguments: str,
    principal: Principal,
    store: PostgresStore,
) -> str:
    """Run one closed-registry tool, or refuse without writing."""

    tool = _TOOLS_BY_NAME.get(name)
    if tool is None:
        return "未知工具"
    if not tool.permissions <= principal.permissions:
        return TOOL_DENIED
    try:
        payload = _load_arguments(arguments)
    except ValueError as exc:
        return f"参数不符合要求。{exc}"
    try:
        return tool.handler(payload, store)
    except ValidationError as exc:
        return _format_validation(exc)
    except Conflict as exc:
        return exc.detail


def _assistant_tool_message(turn: ModelTurn) -> dict[str, object]:
    return {
        "role": "assistant",
        "content": turn.content,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in turn.tool_calls
        ],
    }


def _load_arguments(arguments: str) -> object:
    text = arguments.strip()
    if text == "":
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("工具参数不是 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("工具参数不是 JSON 对象")
    return value


def _format_validation(exc: ValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors():
        loc = ".".join(str(item) for item in error.get("loc", ()))
        message = str(error.get("msg", "无效"))
        parts.append(f"{loc}: {message}" if loc else message)
    detail = "；".join(parts) if parts else "无效"
    return f"参数不符合要求。{detail}"


for _tool in _TOOLS:
    _unknown = _tool.permissions - ALL_PERMISSIONS
    if _unknown:
        raise RuntimeError(f"tool {_tool.name} references unknown permissions: {_unknown}")
