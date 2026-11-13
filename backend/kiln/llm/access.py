"""Caller model set for one request.

The provider object is built once at startup and is not rebuilt here. Chat
passes the result into ``resolve_model``. ``source=env`` keeps today's global
catalog. ``source=db`` uses the saved catalog and the caller's roles.
"""

from dataclasses import dataclass

from kiln.auth import Principal
from kiln.config import Settings
from kiln.db.llm_catalog import LlmCatalogStore
from kiln.domain.llm_catalog import ROLE_ORDER, CatalogSnapshot, env_snapshot


@dataclass(frozen=True)
class ChatAllowance:
    allowed: tuple[str, ...]
    default: str
    managed: bool


@dataclass(frozen=True)
class CallerModels:
    allowed: tuple[str, ...]
    default_model: str | None


def effective_snapshot(settings: Settings, store: LlmCatalogStore) -> CatalogSnapshot:
    stored = store.read()
    if stored is None:
        return env_snapshot(settings)
    return stored


def provider_models(
    settings: Settings,
    principal: Principal,
    snapshot: CatalogSnapshot,
) -> tuple[list[str], str | None]:
    """``models`` and ``default_model`` for ``GET /llm/provider``.

    A caller without ``agent.qa`` gets an empty ``models`` list. ``base_url``
    is unchanged and the route stays login-only. ``mock`` and ``off`` always
    publish an empty ``models`` list. A managed ``default_model`` is the
    caller default, or null.
    """

    if snapshot.source != "db":
        models = list(settings.llm_catalog())
        default_model = settings.llm_model or None
    else:
        caller = caller_models(principal, snapshot)
        default_model = caller.default_model
        models = list(caller.allowed) if settings.llm_provider == "openai" else []
    if "agent.qa" not in principal.permissions:
        return [], default_model
    return models, default_model


def chat_allowance(
    settings: Settings,
    principal: Principal,
    snapshot: CatalogSnapshot,
) -> ChatAllowance:
    """Allowance for one openai chat request. Not used for mock or off.

    An unmanaged deployment with environment models keeps that allow-list.
    With no environment models, only ``LLM_MODEL`` is allowed when it is set.
    When neither is set, the empty list is managed and chat raises
    ``NO_MODELS_FOR_ACCOUNT`` instead of forwarding an arbitrary id.
    """

    if snapshot.source != "db":
        catalog = tuple(settings.llm_catalog())
        if catalog:
            return ChatAllowance(
                allowed=catalog,
                default=settings.llm_request_default(),
                managed=False,
            )
        if settings.llm_model:
            return ChatAllowance(
                allowed=(settings.llm_model,),
                default=settings.llm_model,
                managed=False,
            )
        return ChatAllowance(allowed=(), default="", managed=True)
    caller = caller_models(principal, snapshot)
    return ChatAllowance(
        allowed=caller.allowed,
        default=caller.default_model or "",
        managed=True,
    )


def caller_models(principal: Principal, snapshot: CatalogSnapshot) -> CallerModels:
    """Intersection of the platform catalog and the caller's role assignments.

    The default is the global default when it is in that intersection.
    Otherwise it is the first held role default in reader, builder, publisher,
    admin order that is still in the intersection. Otherwise it is the first
    catalog id in the intersection. A machine token receives the whole catalog.
    """

    catalog = snapshot.model_ids()
    catalog_set = set(catalog)
    global_default = snapshot.default_model if snapshot.default_model in catalog_set else None
    if principal.kind == "machine":
        default = global_default or (catalog[0] if catalog else None)
        return CallerModels(catalog, default)

    held = set(principal.account.role_ids) if principal.account is not None else set()
    by_role = {item.role_id: item for item in snapshot.assignments}
    assigned: set[str] = set()
    for role_id in held:
        item = by_role.get(role_id)
        if item is None:
            continue
        assigned.update(model_id for model_id in item.models if model_id in catalog_set)
    allowed = tuple(model_id for model_id in catalog if model_id in assigned)
    allowed_set = set(allowed)
    if global_default is not None and global_default in allowed_set:
        return CallerModels(allowed, global_default)
    for role_id in ROLE_ORDER:
        if role_id not in held:
            continue
        item = by_role.get(role_id)
        if item is None or item.default_model is None:
            continue
        if item.default_model in allowed_set:
            return CallerModels(allowed, item.default_model)
    return CallerModels(allowed, allowed[0] if allowed else None)
