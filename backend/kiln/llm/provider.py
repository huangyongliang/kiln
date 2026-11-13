"""OpenAI-compatible chat provider, plus mock and off modes.

`openai` posts to `{LLM_BASE_URL}/chat/completions`. The chat route may pass a
server-owned `tools` array into `complete_turn`. `mock` and `off` only implement
`complete` and never receive that array, so their completions leave `actions`
empty. They exist so local work can continue before an endpoint is ready; they
are not the only path.

The optional model catalog is not fetched from the endpoint. ``resolve_model``
receives the allow-list and default for this request. The provider object is
not rebuilt per request. ``mock`` and ``off`` do not check a catalog.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field

from kiln.config import Settings
from kiln.domain.llm_catalog import MODEL_NOT_ALLOWED, MODEL_UNSPECIFIED, NO_MODELS_FOR_ACCOUNT
from kiln.errors import ModelDisabled, ModelRequestError, ModelTimeout, ModelUpstreamError


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[ChatMessage] = Field(min_length=1, max_length=32)
    model: str | None = Field(
        default=None,
        max_length=128,
        description=(
            "可选。openai 且该调用者的允许列表非空时必须是其中的 id；省略则用该调用者的默认。"
            "允许列表为空时不再转发。"
        ),
    )


class ChatAction(BaseModel):
    """A write this turn completed. Failures stay in the tool result only."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["skill_drafted", "skill_updated", "build_started"]
    id: UUID
    name: str


class ChatCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    provider: Literal["openai", "mock"]
    model: str
    content: str
    finish_reason: str
    actions: list[ChatAction] = Field(default_factory=list)


class ProviderStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["openai", "mock", "off"]
    base_url: str | None
    base_url_configured: bool
    api_key_configured: bool
    default_model: str | None
    models: list[str] = Field(
        default_factory=list,
        description=(
            "可选模型 id。没有 agent.qa 的调用者得到空列表。"
            "尚未托管且调用者具备 agent.qa 时，openai 为环境变量目录；"
            "已托管且提供方为 openai 时，为当前调用者可用的 id。"
            "mock 与 off 始终为空。不从上游拉取。"
        ),
    )


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ModelTurn:
    """One chat/completions response. Tool calls stay inside the server loop."""

    id: str
    model: str
    content: str | None
    finish_reason: str
    tool_calls: tuple[ToolCall, ...]


class LLMProvider(Protocol):
    name: str

    def complete(
        self,
        request: ChatRequest,
        *,
        allowed: Sequence[str],
        default: str,
        managed: bool,
    ) -> ChatCompletion: ...


class DisabledProvider:
    name = "off"

    def complete(
        self,
        request: ChatRequest,
        *,
        allowed: Sequence[str],
        default: str,
        managed: bool,
    ) -> ChatCompletion:
        del request, allowed, default, managed
        raise ModelDisabled()


class MockProvider:
    name = "mock"

    def complete(
        self,
        request: ChatRequest,
        *,
        allowed: Sequence[str],
        default: str,
        managed: bool,
    ) -> ChatCompletion:
        del allowed, default, managed
        preview = next(
            (item.content for item in reversed(request.messages) if item.role == "user"),
            "",
        )
        preview = preview[:200]
        return ChatCompletion(
            id=f"mock-{uuid4().hex[:12]}",
            provider="mock",
            model=(request.model or "mock").strip() or "mock",
            content=f"这是模拟回复，未调用外部模型。收到：{preview}",
            finish_reason="stop",
        )


class OpenAICompatibleProvider:
    """Calls an OpenAI-compatible chat completions endpoint."""

    name = "openai"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        client: httpx.Client,
    ) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._client = client

    def resolve_model(
        self,
        requested: str | None,
        *,
        allowed: Sequence[str],
        default: str,
        managed: bool,
    ) -> str:
        """Return the model id for this request.

        ``allowed`` and ``default`` belong to this caller. They are computed
        from the caller's roles and the managed flag, then passed in. This
        method does not read a catalog captured at startup.

        A non-empty list requires the id to be in that list. A blank or
        omitted id uses ``default``. An id outside the list raises
        ``ModelRequestError`` and must not be sent upstream.

        An empty list with ``managed`` true raises ``NO_MODELS_FOR_ACCOUNT``
        and does not forward an id. Chat uses that when the catalog is saved
        and empty, and when an unmanaged openai deployment has neither
        ``LLM_MODELS`` nor ``LLM_MODEL``. ``managed`` false with an empty list
        still forwards a request model; the chat route does not use that
        combination.
        """

        chosen = (requested or "").strip()
        catalog = tuple(allowed)
        fallback = default.strip()
        if managed and not catalog:
            raise ModelRequestError(NO_MODELS_FOR_ACCOUNT)
        if catalog:
            model = chosen or fallback
            if not model:
                raise ModelRequestError(MODEL_UNSPECIFIED)
            if model not in catalog:
                raise ModelRequestError(MODEL_NOT_ALLOWED)
            return model
        model = chosen or fallback
        if not model:
            raise ModelRequestError(MODEL_UNSPECIFIED)
        return model

    def complete(
        self,
        request: ChatRequest,
        *,
        allowed: Sequence[str],
        default: str,
        managed: bool,
    ) -> ChatCompletion:
        """Require this caller's allowance. There is no unrestricted fallback.

        ``allowed``, ``default``, and ``managed`` are required keyword
        arguments. Omitting any of them raises ``TypeError`` and does not
        choose a model. The chat route does not use this method for openai;
        it passes the same allowance into the tool loop.
        """

        messages = [{"role": item.role, "content": item.content} for item in request.messages]
        model = self.resolve_model(
            request.model,
            allowed=allowed,
            default=default,
            managed=managed,
        )
        turn = self.complete_turn(model=model, messages=messages, tools=None)
        if turn.tool_calls or not turn.content:
            raise ModelUpstreamError("模型端点返回了无法识别的响应")
        return ChatCompletion(
            id=turn.id,
            provider="openai",
            model=turn.model,
            content=turn.content,
            finish_reason=turn.finish_reason,
        )

    def complete_turn(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> ModelTurn:
        """Post one chat/completions request.

        ``model`` is already resolved. Every round of one chat request must
        pass that same id. This method does not consult the catalog again.
        ``tools`` is omitted when None.
        """

        model = model.strip()
        if not model:
            raise ModelRequestError("未指定模型。请在请求中传入 model，或设置 LLM_MODEL。")
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload: dict[str, Any] = {"model": model, "messages": messages}
        if tools is not None:
            payload["tools"] = tools
        try:
            response = self._client.post(
                chat_completions_url(self._base_url),
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise ModelTimeout() from exc
        except httpx.HTTPStatusError as exc:
            raise ModelUpstreamError(f"模型端点返回 {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise ModelUpstreamError("无法连接模型端点") from exc
        return _parse_turn(response, model=model)


def build_provider(settings: Settings, client: httpx.Client) -> LLMProvider:
    if settings.llm_provider == "off":
        return DisabledProvider()
    if settings.llm_provider == "mock":
        return MockProvider()
    return OpenAICompatibleProvider(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key_value(),
        client=client,
    )


def provider_status(
    settings: Settings,
    *,
    models: list[str],
    default_model: str | None,
) -> ProviderStatus:
    """Publish the configured provider.

    ``models`` and ``default_model`` are already chosen for this caller.
    ``mock`` and ``off`` pass an empty ``models`` list even if ``LLM_MODELS``
    is set. ``base_url`` is still echoed; that is existing behavior. The key
    itself is not returned.
    """

    base_url = settings.llm_base_url or None
    return ProviderStatus(
        provider=settings.llm_provider,
        base_url=base_url,
        base_url_configured=base_url is not None,
        api_key_configured=bool(settings.llm_api_key_value()),
        default_model=default_model,
        models=models,
    )


def chat_completions_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def _parse_turn(response: httpx.Response, *, model: str) -> ModelTurn:
    try:
        data = response.json()
        choice = data["choices"][0]
        message = choice["message"]
        if not isinstance(message, dict):
            raise TypeError("message must be an object")
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise TypeError("content must be a string")
        if content == "":
            content = None
        tool_calls = _parse_tool_calls(message)
        if not tool_calls and content is None:
            raise TypeError("content must be a non-empty string")
        finish_reason = choice.get("finish_reason") or "stop"
        if not isinstance(finish_reason, str):
            raise TypeError("finish_reason must be a string")
        completion_id = data.get("id") or f"chatcmpl-{uuid4().hex[:12]}"
        if not isinstance(completion_id, str) or completion_id == "":
            raise TypeError("id must be a non-empty string")
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ModelUpstreamError("模型端点返回了无法识别的响应") from exc
    return ModelTurn(
        id=completion_id,
        model=model,
        content=content,
        finish_reason=finish_reason,
        tool_calls=tuple(tool_calls),
    )


def _parse_tool_calls(message: dict[str, Any]) -> list[ToolCall]:
    raw = message.get("tool_calls")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TypeError("tool_calls must be a list")
    calls: list[ToolCall] = []
    for item in raw:
        if not isinstance(item, dict):
            raise TypeError("tool_call must be an object")
        call_type = item.get("type", "function")
        if call_type != "function":
            raise TypeError("tool_call type must be function")
        call_id = item.get("id")
        function = item.get("function")
        if not isinstance(call_id, str) or call_id == "":
            raise TypeError("tool_call id must be a non-empty string")
        if not isinstance(function, dict):
            raise TypeError("tool_call function must be an object")
        name = function.get("name")
        arguments = function.get("arguments")
        if not isinstance(name, str) or name == "":
            raise TypeError("tool name must be a non-empty string")
        if isinstance(arguments, str):
            argument_text = arguments
        elif isinstance(arguments, dict):
            argument_text = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
        elif arguments is None:
            argument_text = "{}"
        else:
            raise TypeError("tool arguments must be a JSON string")
        calls.append(ToolCall(id=call_id, name=name, arguments=argument_text))
    return calls
