"""Chat outline routed through the configured model provider.

POST /llm/chat is the only chat entry and still requires agent.qa. The
OpenAI provider runs a server-owned tool loop. When its catalog is non-empty,
the request model must be in that catalog, and every round of the request
uses the same resolved id. mock and off keep complete() and do not validate
the model against the catalog. They do not attach or execute tools.
"""

from fastapi import APIRouter, Depends, Request

from kiln.api.deps import get_build_store, get_llm, get_settings, get_store
from kiln.auth import Principal, require_auth, require_permission
from kiln.config import Settings
from kiln.db.builds import BuildStore
from kiln.db.store import PostgresStore
from kiln.llm.provider import (
    ChatCompletion,
    ChatRequest,
    LLMProvider,
    OpenAICompatibleProvider,
    ProviderStatus,
    provider_status,
)
from kiln.llm.tools import ToolContext, run_tool_loop

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
    request: Request,
    principal: Principal = Depends(require_permission("agent.qa")),
    provider: LLMProvider = Depends(get_llm),
    store: PostgresStore = Depends(get_store),
    builds: BuildStore = Depends(get_build_store),
    settings: Settings = Depends(get_settings),
) -> ChatCompletion:
    if isinstance(provider, OpenAICompatibleProvider):
        return run_tool_loop(
            payload,
            provider,
            principal,
            ToolContext(
                catalog=store,
                builds=builds,
                jenkins=request.app.state.jenkins,
                git_source=request.app.state.git_source,
                settings=settings,
            ),
        )
    return provider.complete(payload)
