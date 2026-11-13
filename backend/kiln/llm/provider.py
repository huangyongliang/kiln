"""OpenAI-compatible chat provider, plus mock and off modes.

`openai` posts to `{LLM_BASE_URL}/chat/completions`. The chat route may pass a
server-owned `tools` array into `complete_turn`. `mock` and `off` only implement
`complete` and never receive that array, so their completions leave `actions`
empty. They exist so local work can continue before an endpoint is ready; they
are not the only path.

The optional model catalog is the ``LLM_MODELS`` allow-list (plus ``LLM_MODEL``
when that default is missing from the list). It is not fetched from the
endpoint. ``mock`` and ``off`` publish an empty catalog and do not check it.
"""

import json
from dataclasses import dataclass
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field

from kiln.config import Settings
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
            "可选。openai 且目录非空时必须是目录中的 id；省略则用默认。"
            "目录为空时仍按原样转发。"
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
        description="可选模型 id。openai 且已配置时为有效目录；mock 与 off 为空。不从上游拉取。",
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

    def complete(self, request: ChatRequest) -> ChatCompletion: ...


class DisabledProvider:
    name = "off"

    def complete(self, request: ChatRequest) -> ChatCompletion:
        del request
        raise ModelDisabled()


class MockProvider:
    name = "mock"

    def complete(self, request: ChatRequest) -> ChatCompletion:
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
        default_model: str,
        catalog: list[str],
        client: httpx.Client,
    ) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._default_model = default_model
        self._catalog = tuple(catalog)
        self._client = client

    def resolve_model(self, requested: str | None) -> str:
        """Return the model id for this request.

        A non-empty catalog requires the id to be in that catalog. A blank or
        omitted id uses the request default (``LLM_MODEL``, or the first
        catalog item). An id outside the catalog raises ``ModelRequestError``
        and must not be sent upstream.

        An empty catalog keeps the previous compatibility for existing
        scripts: a request that carries a model is forwarded as-is. If both
        the request and ``LLM_MODEL`` are missing, raise ``ModelRequestError``.
        The console does not offer a free-text model id on that path.
        """

        chosen = (requested or "").strip()
        if self._catalog:
            model = chosen or self._default_model
            if not model:
                raise ModelRequestError("未指定模型。请在请求中传入 model，或设置 LLM_MODEL。")
            if model not in self._catalog:
                raise ModelRequestError("模型不在允许的模型列表中。")
            return model
        model = chosen or self._default_model
        if not model:
            raise ModelRequestError("未指定模型。请在请求中传入 model，或设置 LLM_MODEL。")
        return model

    def complete(self, request: ChatRequest) -> ChatCompletion:
        messages = [{"role": item.role, "content": item.content} for item in request.messages]
        model = self.resolve_model(request.model)
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
        default_model=settings.llm_request_default(),
        catalog=settings.llm_catalog(),
        client=client,
    )


def provider_status(settings: Settings) -> ProviderStatus:
    """Publish the configured provider. ``models`` is the openai catalog only.

    ``default_model`` stays ``LLM_MODEL`` when that value is set, and is null
    when it is empty. A request that omits ``model``, and the console's
    preselection, then use the first catalog item. ``mock`` and ``off`` always
    publish ``models: []`` even if ``LLM_MODELS`` is set. ``base_url`` is still
    echoed; that is existing behavior.
    """

    base_url = settings.llm_base_url or None
    default_model = settings.llm_model or None
    return ProviderStatus(
        provider=settings.llm_provider,
        base_url=base_url,
        base_url_configured=base_url is not None,
        api_key_configured=bool(settings.llm_api_key_value()),
        default_model=default_model,
        models=settings.llm_catalog(),
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
