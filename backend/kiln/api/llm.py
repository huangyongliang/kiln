"""Chat outline routed through the configured model provider.

POST /llm/chat is the only chat entry and still requires agent.qa. The
OpenAI provider runs a server-owned tool loop. resolve_model receives the
allow-list and default for this caller. The provider is the process singleton.
mock and off accept the same complete() allowance arguments and ignore them.
They do not validate the model against a catalog, and they do not attach or
execute tools.

GET / PUT /llm/catalog requires llm.manage. A caller missing that permission
gets 403 with the usual detail string, and the string names llm.manage.
PUT replaces the catalog, the four role assignments, and the managed flag in
one transaction. The last writer wins. This route does not use an ETag.
"""

from fastapi import APIRouter, Depends, Request

from kiln.api.deps import get_build_store, get_llm, get_llm_catalog, get_settings, get_store
from kiln.auth import Principal, require_auth, require_permission
from kiln.config import Settings
from kiln.db.builds import BuildStore
from kiln.db.llm_catalog import LlmCatalogStore
from kiln.db.store import PostgresStore
from kiln.domain.llm_catalog import (
    CatalogDocument,
    CatalogReplace,
    document_from,
    validate_catalog_replace,
)
from kiln.llm.access import chat_allowance, effective_snapshot, provider_models
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

_CATALOG_FORBIDDEN = "没有权限（需要：llm.manage）"


@router.get("/provider", response_model=ProviderStatus, summary="查看模型提供方")
def read_provider(
    principal: Principal = Depends(require_auth),
    settings: Settings = Depends(get_settings),
    catalog: LlmCatalogStore = Depends(get_llm_catalog),
) -> ProviderStatus:
    models, default_model = provider_models(
        settings,
        principal,
        effective_snapshot(settings, catalog),
    )
    return provider_status(settings, models=models, default_model=default_model)


@router.get(
    "/catalog",
    response_model=CatalogDocument,
    summary="查看模型目录与角色分配",
    dependencies=[Depends(require_permission("llm.manage", detail=_CATALOG_FORBIDDEN))],
)
def read_catalog(
    settings: Settings = Depends(get_settings),
    catalog: LlmCatalogStore = Depends(get_llm_catalog),
) -> CatalogDocument:
    """Return the saved catalog, or the environment seed when nothing is saved.

    The seed uses LLM_MODEL and LLM_MODELS even when the provider is mock or
    off. GET /llm/provider still returns an empty models list in those modes.
    The response has no endpoint and no secret.
    """

    return document_from(effective_snapshot(settings, catalog), settings)


@router.put(
    "/catalog",
    response_model=CatalogDocument,
    summary="替换模型目录与角色分配",
    description=(
        "在一个事务里整体替换模型目录、四个固定角色的分配和托管状态。"
        "后写入的请求生效。这一接口不使用 ETag。"
        "保存成功后 source 为 db，包括保存空目录。"
        "之后修改 LLM_MODEL 或 LLM_MODELS 不会覆盖已保存的内容。"
        "不写入端点或密钥。"
    ),
    dependencies=[Depends(require_permission("llm.manage", detail=_CATALOG_FORBIDDEN))],
)
def replace_catalog(
    payload: CatalogReplace,
    settings: Settings = Depends(get_settings),
    catalog: LlmCatalogStore = Depends(get_llm_catalog),
) -> CatalogDocument:
    snapshot = validate_catalog_replace(payload)
    catalog.replace(snapshot)
    return document_from(snapshot, settings)


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
    catalog: LlmCatalogStore = Depends(get_llm_catalog),
) -> ChatCompletion:
    if isinstance(provider, OpenAICompatibleProvider):
        allowance = chat_allowance(
            settings,
            principal,
            effective_snapshot(settings, catalog),
        )
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
            allowed=allowance.allowed,
            default=allowance.default,
            managed=allowance.managed,
        )
    return provider.complete(payload, allowed=(), default="", managed=False)
