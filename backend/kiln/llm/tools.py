"""Closed tool registry for 对话.

The client cannot supply tools. The chat route runs this loop only for the
OpenAI-compatible provider and attaches the schema itself. ``mock`` and ``off``
never enter the loop, so they neither invent nor execute tools.

A caller missing a tool's permission codes does not write. The tool result is
「没有权限」, and the loop continues until the model returns content or the
round cap is reached. The cap is a code constant, not an environment variable.

The loop prepends one platform system prompt and drops client ``system``
messages so they cannot replace that contract. There is no submit or publish
tool. ``build_artifact`` starts a product build the same way as POST /builds.
It needs ``agent.build_artifact``, ``executor.run``, and ``skill.use``.
"""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, ValidationError

from kiln.auth import Principal
from kiln.config import Settings
from kiln.db.builds import BuildStore
from kiln.db.store import PostgresStore
from kiln.domain.builds import ProductBuildCreate
from kiln.domain.rbac import ALL_PERMISSIONS
from kiln.domain.skills import (
    SkillCreate,
    SkillList,
    SkillScope,
    SkillStatus,
    may_revise_skill,
)
from kiln.errors import (
    GIT_SOURCE_UNCONFIGURED_DETAIL,
    JENKINS_UNCONFIGURED_DETAIL,
    BadRequest,
    Conflict,
    ModelUpstreamError,
    NotConfigured,
    NotFound,
    Unprocessable,
    UpstreamFailure,
)
from kiln.executor.jenkins import JenkinsClient
from kiln.executor.product import create_product_build, publish
from kiln.git.source import GitSource
from kiln.llm.provider import (
    ChatAction,
    ChatCompletion,
    ChatRequest,
    ModelTurn,
    OpenAICompatibleProvider,
)

# Model rounds, not an environment variable. One round is one chat/completions call.
MAX_MODEL_ROUNDS = 4

TOOL_DENIED = "没有权限"
ROUND_CAP_CONTENT = "已达到对话工具轮次上限。"
DRAFT_ONLY = "只有草稿可以修改"

# Fixed contract for the model. Client system messages are discarded.
PLATFORM_SYSTEM_PROMPT = (
    "你协助用户产出可以送审的 Skill。"
    "步骤是数据，不会执行。"
    "你不能提交审核，也不能发布。"
    "只有草稿状态可以改稿。"
    "构建必须使用已发布的 Skill。"
    "用 list_skills 和 get_skill 查询「我产出的」和「我能用的已发布 Skill」。"
    "起草或改稿成功后，告诉用户到 Skill 页提交审核。"
)

@dataclass(frozen=True)
class ToolContext:
    """Catalog plus the same build dependencies POST /builds already uses.

    ``jenkins`` and ``git_source`` stay None when those endpoints are not
    configured. The tool reports that as text. It does not turn the chat
    request into 503.
    """

    catalog: PostgresStore
    builds: BuildStore
    jenkins: JenkinsClient | None
    git_source: GitSource | None
    settings: Settings


@dataclass(frozen=True)
class ToolResult:
    content: str
    action: ChatAction | None = None


_Handler = Callable[[object, ToolContext, Principal], ToolResult]


class ListSkillsArguments(BaseModel):
    """Optional status and scope. Same values as GET /skills. No other fields."""

    model_config = ConfigDict(extra="forbid")

    status: SkillStatus | None = None
    scope: SkillScope | None = None


class GetSkillArguments(BaseModel):
    """Fetch one skill by id. No other fields."""

    model_config = ConfigDict(extra="forbid")

    id: UUID


class UpdateSkillArguments(SkillCreate):
    """Full replacement of a draft, plus the id of the skill to update."""

    id: UUID


@dataclass(frozen=True)
class _Tool:
    name: str
    description: str
    permissions: frozenset[str]
    parameters: type[BaseModel]
    handler: _Handler


def _json(model: BaseModel) -> str:
    return json.dumps(jsonable_encoder(model), ensure_ascii=False, separators=(",", ":"))


def _account_id(principal: Principal) -> UUID | None:
    if principal.account is None:
        return None
    return principal.account.id


def _list_skills(payload: object, context: ToolContext, principal: Principal) -> ToolResult:
    parsed = ListSkillsArguments.model_validate(payload)
    if parsed.scope is SkillScope.usable and "skill.use" not in principal.permissions:
        return ToolResult(TOOL_DENIED)
    listed = SkillList(
        items=context.catalog.list_skills(
            parsed.status,
            scope=parsed.scope,
            account_id=_account_id(principal),
        )
    )
    return ToolResult(_json(listed))


def _get_skill(payload: object, context: ToolContext, principal: Principal) -> ToolResult:
    del principal
    parsed = GetSkillArguments.model_validate(payload)
    return ToolResult(_json(context.catalog.get_skill(parsed.id)))


def _draft_skill(payload: object, context: ToolContext, principal: Principal) -> ToolResult:
    created = context.catalog.create_skill(
        SkillCreate.model_validate(payload),
        _account_id(principal),
    )
    return ToolResult(
        _json(created),
        ChatAction(type="skill_drafted", id=created.id, name=created.name),
    )


def _update_skill(payload: object, context: ToolContext, principal: Principal) -> ToolResult:
    parsed = UpdateSkillArguments.model_validate(payload)
    current = context.catalog.get_skill(parsed.id)
    if not may_revise_skill(principal.permissions, _account_id(principal), current):
        return ToolResult(TOOL_DENIED)
    if current.status != SkillStatus.draft:
        return ToolResult(DRAFT_ONLY)
    updated = context.catalog.update_skill(parsed.id, parsed)
    return ToolResult(
        _json(updated),
        ChatAction(type="skill_updated", id=updated.id, name=updated.name),
    )


def _build_artifact(payload: object, context: ToolContext, principal: Principal) -> ToolResult:
    del principal
    created = ProductBuildCreate.model_validate(payload)
    jenkins = context.jenkins
    source = context.git_source
    if jenkins is None:
        raise NotConfigured(JENKINS_UNCONFIGURED_DETAIL)
    if source is None:
        raise NotConfigured(GIT_SOURCE_UNCONFIGURED_DETAIL)
    stored = create_product_build(
        context.builds,
        context.catalog,
        jenkins,
        source,
        context.settings,
        created,
    )
    published = publish(context.builds, stored)
    return ToolResult(
        _json(published),
        ChatAction(type="build_started", id=published.id, name=published.name),
    )


_TOOLS: tuple[_Tool, ...] = (
    _Tool(
        name="list_skills",
        description=(
            "列出 skill。可按 status 筛选 draft、in_review 或 published。"
            "可选 scope：mine 是「我产出的」，usable 是「我能用的已发布 Skill」。"
            "省略 scope 时与 GET /skills 相同，返回全库列表。"
            "scope 可与 status 同时使用。"
        ),
        permissions=frozenset({"skill.read"}),
        parameters=ListSkillsArguments,
        handler=_list_skills,
    ),
    _Tool(
        name="get_skill",
        description="按 id 查看一条 skill。参数不符或没有这条 skill 时，错误写在工具结果里。",
        permissions=frozenset({"skill.read"}),
        parameters=GetSkillArguments,
        handler=_get_skill,
    ),
    _Tool(
        name="draft_skill",
        description=(
            "创建一条 skill 草稿。参数与创建草稿相同，包含名称、版本、输入、步骤和范围。"
            "只保存草稿，不提交审核，不发布，不执行步骤。"
            "成功后告诉用户到 Skill 页提交审核。"
        ),
        permissions=frozenset({"agent.draft_skill", "skill.use"}),
        parameters=SkillCreate,
        handler=_draft_skill,
    ),
    _Tool(
        name="update_skill",
        description=(
            "完整替换一条草稿的可变字段。参数与创建草稿相同，并额外需要 id。"
            "只更新草稿，不改变状态，不清空退回记录，不提交审核，不发布，不执行步骤。"
            "审核中或已发布不能改。成功后告诉用户到 Skill 页提交审核。"
        ),
        permissions=frozenset({"agent.draft_skill", "skill.use"}),
        parameters=UpdateSkillArguments,
        handler=_update_skill,
    ),
    _Tool(
        name="build_artifact",
        description=(
            "发起与 POST /builds 相同的产品构建。"
            "必须提供已发布 skill 的 skill_id，以及仓库、引用、制品名、版本、包类型和可选名称。"
            "仓库须落在该 skill 的允许列表内；列表为空时允许代码源能解析的仓库。"
            "只开始构建，不等待结束，不下载或保存制品字节，不执行 skill 步骤。"
        ),
        permissions=frozenset({"agent.build_artifact", "executor.run", "skill.use"}),
        parameters=ProductBuildCreate,
        handler=_build_artifact,
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
    context: ToolContext,
    *,
    allowed: Sequence[str],
    default: str,
    managed: bool,
) -> ChatCompletion:
    """Call the model at most MAX_MODEL_ROUNDS times, executing only known tools.

    ``allowed``, ``default``, and ``managed`` are required. The chat route
    passes this caller's allowance and does not rebuild the provider. Omitting
    any of the three raises ``TypeError``. The loop does not substitute the
    environment catalog.
    """

    messages = _model_messages(request)
    actions: list[ChatAction] = []
    turn: ModelTurn | None = None
    # Resolve once. Later rounds of this request reuse the same id. The
    # allow-list and default are this caller's, passed in by the chat route.
    # An id outside a non-empty list fails here and does not call the endpoint.
    model = provider.resolve_model(
        request.model,
        allowed=allowed,
        default=default or "",
        managed=managed,
    )
    for _ in range(MAX_MODEL_ROUNDS):
        turn = provider.complete_turn(
            model=model,
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
                actions=actions,
            )
        messages.append(_assistant_tool_message(turn))
        for call in turn.tool_calls:
            outcome = execute_tool(call.name, call.arguments, principal, context)
            if outcome.action is not None:
                actions.append(outcome.action)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": outcome.content,
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
        actions=actions,
    )


def execute_tool(
    name: str,
    arguments: str,
    principal: Principal,
    context: ToolContext,
) -> ToolResult:
    """Run one closed-registry tool, or refuse without writing."""

    tool = _TOOLS_BY_NAME.get(name)
    if tool is None:
        return ToolResult("未知工具")
    if not tool.permissions <= principal.permissions:
        return ToolResult(TOOL_DENIED)
    try:
        payload = _load_arguments(arguments)
    except ValueError as exc:
        return ToolResult(f"参数不符合要求。{exc}")
    try:
        return tool.handler(payload, context, principal)
    except ValidationError as exc:
        return ToolResult(_format_validation(exc))
    except (BadRequest, Conflict, NotConfigured, NotFound, Unprocessable, UpstreamFailure) as exc:
        return ToolResult(exc.detail)


def _model_messages(request: ChatRequest) -> list[dict[str, object]]:
    """Platform system prompt first. Client system messages are dropped."""

    messages: list[dict[str, object]] = [
        {"role": "system", "content": PLATFORM_SYSTEM_PROMPT},
    ]
    for item in request.messages:
        if item.role == "system":
            continue
        messages.append({"role": item.role, "content": item.content})
    return messages


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
