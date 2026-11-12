"""Chat outline routed through the configured model provider."""

from fastapi import APIRouter, Depends

from kiln.api.deps import get_llm, get_settings
from kiln.config import Settings
from kiln.llm.provider import (
    ChatCompletion,
    ChatRequest,
    LLMProvider,
    ProviderStatus,
    provider_status,
)

router = APIRouter(prefix="/llm", tags=["模型"])


@router.get("/provider", response_model=ProviderStatus, summary="查看模型提供方")
def read_provider(settings: Settings = Depends(get_settings)) -> ProviderStatus:
    return provider_status(settings)


@router.post("/chat", response_model=ChatCompletion, summary="对话补全")
def chat(payload: ChatRequest, provider: LLMProvider = Depends(get_llm)) -> ChatCompletion:
    return provider.complete(payload)
