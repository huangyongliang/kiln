"""Chat outline routed through the configured model provider.

POST /llm/chat is the only chat entry and still requires agent.qa. The
OpenAI provider runs a server-owned tool loop. mock and off keep complete()
and do not attach or execute tools.
"""

from fastapi import APIRouter, Depends

from kiln.api.deps import get_llm, get_settings, get_store
from kiln.auth import Principal, require_auth, require_permission
from kiln.config import Settings
from kiln.db.store import PostgresStore
from kiln.llm.provider import (
    ChatCompletion,
    ChatRequest,
    LLMProvider,
    OpenAICompatibleProvider,
    ProviderStatus,
    provider_status,
)
from kiln.llm.tools import run_tool_loop

router = APIRouter(
    prefix="/llm",
    tags=["模型"],
    dependencies=[Depends(require_auth)],
)


@router.get("/provider", response_model=ProviderStatus, summary="查看模型提供方")
def read_provider(settings: Settings = Depends(get_settings)) -> ProviderStatus:
    return provider_status(settings)


@router.post(
    "/chat",
    response_model=ChatCompletion,
    summary="对话补全",
    dependencies=[Depends(require_permission("agent.qa"))],
)
def chat(
    payload: ChatRequest,
    principal: Principal = Depends(require_permission("agent.qa")),
    provider: LLMProvider = Depends(get_llm),
    store: PostgresStore = Depends(get_store),
) -> ChatCompletion:
    if isinstance(provider, OpenAICompatibleProvider):
        return run_tool_loop(payload, provider, principal, store)
    return provider.complete(payload)
