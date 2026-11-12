"""OpenAI-compatible chat provider, plus mock and off modes.

`openai` posts to `{LLM_BASE_URL}/chat/completions`. `mock` and `off` exist so
local work can continue before an endpoint is ready; they are not the only path.
"""

from typing import Literal, Protocol
from uuid import uuid4

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
    model: str | None = Field(default=None, max_length=128)


class ChatCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    provider: Literal["openai", "mock"]
    model: str
    content: str
    finish_reason: str


class ProviderStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["openai", "mock", "off"]
    base_url: str | None
    base_url_configured: bool
    api_key_configured: bool
    default_model: str | None


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
        client: httpx.Client,
    ) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._default_model = default_model
        self._client = client

    def complete(self, request: ChatRequest) -> ChatCompletion:
        model = (request.model or self._default_model).strip()
        if not model:
            raise ModelRequestError("未指定模型。请在请求中传入 model，或设置 LLM_MODEL。")
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload = {
            "model": model,
            "messages": [{"role": item.role, "content": item.content} for item in request.messages],
        }
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
        return _parse_completion(response, model=model)


def build_provider(settings: Settings, client: httpx.Client) -> LLMProvider:
    if settings.llm_provider == "off":
        return DisabledProvider()
    if settings.llm_provider == "mock":
        return MockProvider()
    return OpenAICompatibleProvider(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key_value(),
        default_model=settings.llm_model,
        client=client,
    )


def provider_status(settings: Settings) -> ProviderStatus:
    base_url = settings.llm_base_url or None
    default_model = settings.llm_model or None
    return ProviderStatus(
        provider=settings.llm_provider,
        base_url=base_url,
        base_url_configured=base_url is not None,
        api_key_configured=bool(settings.llm_api_key_value()),
        default_model=default_model,
    )


def chat_completions_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def _parse_completion(response: httpx.Response, *, model: str) -> ChatCompletion:
    try:
        data = response.json()
        choice = data["choices"][0]
        content = choice["message"]["content"]
        if not isinstance(content, str) or content == "":
            raise TypeError("content must be a non-empty string")
        finish_reason = choice.get("finish_reason") or "stop"
        if not isinstance(finish_reason, str):
            raise TypeError("finish_reason must be a string")
        completion_id = data.get("id") or f"chatcmpl-{uuid4().hex[:12]}"
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ModelUpstreamError("模型端点返回了无法识别的响应") from exc
    return ChatCompletion(
        id=str(completion_id),
        provider="openai",
        model=model,
        content=content,
        finish_reason=finish_reason,
    )
