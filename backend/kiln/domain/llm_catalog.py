"""Platform model catalog and the four fixed role assignments.

Connection settings stay in the environment. This module validates a full
replace of the catalog. It does not store an endpoint or a secret, and it
does not truncate an illegal list.
"""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from kiln.config import LLM_MODEL_ID_MAX_LENGTH, LLM_MODELS_MAX_ITEMS, Settings
from kiln.domain.rbac import ROLES
from kiln.errors import Unprocessable

ROLE_ORDER: tuple[str, ...] = tuple(role.id for role in ROLES)
FIXED_ROLE_IDS = frozenset(ROLE_ORDER)

ID_EMPTY = "模型 id 不能为空。"
ID_TOO_LONG = "模型 id 不能超过 128 个字符，不会截断。"
TOO_MANY = "模型目录不能超过 32 项，不会只保留前 32 项。"
ID_DUPLICATE = "模型 id 重复。"
NAME_TOO_LONG = "显示名不能超过 128 个字符。"
DEFAULT_NOT_IN_CATALOG = "全局默认必须在模型目录中。"
DEFAULT_WHEN_EMPTY = "模型目录为空时不能指定全局默认。"
ROLE_ID_INVALID = "角色 id 必须是 reader、builder、publisher、admin 之一。"
ROLE_SET_INVALID = "必须且只能为 reader、builder、publisher、admin 各提交一份分配。"
ASSIGNMENT_OUTSIDE = "不能把目录外的模型分配给角色。"
ASSIGNMENT_DUPLICATE = "同一角色的可用模型重复。"
ROLE_DEFAULT_NOT_IN_SET = "角色默认必须在该角色的可用模型中。"
ASSIGNMENT_ID_EMPTY = "分配的模型 id 不能为空。"

NO_MODELS_FOR_ACCOUNT = "当前账号没有可用的模型。请联系管理员在模型配置中分配。"
MODEL_NOT_ALLOWED = "模型不在允许的模型列表中。"
MODEL_UNSPECIFIED = "未指定模型。请在请求中传入 model，或设置 LLM_MODEL。"


class CatalogModelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    display_name: str | None = None


class RoleAssignmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: str
    models: list[str]
    default_model: str | None = None


class CatalogReplace(BaseModel):
    """Full replace. The caller does not send source, an endpoint, or a secret."""

    model_config = ConfigDict(extra="forbid")

    models: list[CatalogModelIn]
    default_model: str | None = None
    assignments: list[RoleAssignmentIn]


class CatalogModelOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    display_name: str | None = None


class RoleAssignmentOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: str
    models: list[str]
    default_model: str | None = None


class CatalogDocument(BaseModel):
    """Managed state, read-only connection flags, the catalog, and role assignments.

    ``base_url`` is not included. The existing echo stays on ``GET /llm/provider``.
    """

    model_config = ConfigDict(extra="forbid")

    source: Literal["env", "db"]
    provider: Literal["openai", "mock", "off"]
    base_url_configured: bool
    api_key_configured: bool
    models: list[CatalogModelOut]
    default_model: str | None
    assignments: list[RoleAssignmentOut]


@dataclass(frozen=True)
class CatalogModel:
    id: str
    display_name: str | None


@dataclass(frozen=True)
class RoleAssignment:
    role_id: str
    models: tuple[str, ...]
    default_model: str | None


@dataclass(frozen=True)
class CatalogSnapshot:
    source: Literal["env", "db"]
    models: tuple[CatalogModel, ...]
    default_model: str | None
    assignments: tuple[RoleAssignment, ...]

    def model_ids(self) -> tuple[str, ...]:
        return tuple(item.id for item in self.models)


def validate_catalog_replace(payload: CatalogReplace) -> CatalogSnapshot:
    """Reject an illegal replace. Nothing is shortened to fit."""

    if len(payload.models) > LLM_MODELS_MAX_ITEMS:
        raise Unprocessable(TOO_MANY)
    models: list[CatalogModel] = []
    seen: set[str] = set()
    for item in payload.models:
        model_id = _model_id(item.id, empty=ID_EMPTY)
        if model_id in seen:
            raise Unprocessable(ID_DUPLICATE)
        seen.add(model_id)
        models.append(CatalogModel(id=model_id, display_name=_display_name(item.display_name)))
    default_model = _optional_model_id(payload.default_model)
    if not models and default_model is not None:
        raise Unprocessable(DEFAULT_WHEN_EMPTY)
    if default_model is not None and default_model not in seen:
        raise Unprocessable(DEFAULT_NOT_IN_CATALOG)
    assignments = _assignments(payload.assignments, seen)
    return CatalogSnapshot(
        source="db",
        models=tuple(models),
        default_model=default_model,
        assignments=assignments,
    )


def env_snapshot(settings: Settings) -> CatalogSnapshot:
    """Editable preview of the environment seed. Independent of provider.

    Roles that already have ``agent.qa`` are shown with every seed id, which
    is the runtime behavior before the first save. Reader is not pre-assigned.
    The global default is ``LLM_MODEL`` when set, otherwise the first seed id,
    so saving this preview without edits keeps today's request default.
    """

    ids = settings.llm_seed_ids()
    if settings.llm_model:
        default: str | None = settings.llm_model
    elif ids:
        default = ids[0]
    else:
        default = None
    models = tuple(CatalogModel(id=item, display_name=None) for item in ids)
    assignments: list[RoleAssignment] = []
    for role in ROLES:
        if "agent.qa" in role.permissions and ids:
            assignments.append(RoleAssignment(role.id, tuple(ids), default))
        else:
            assignments.append(RoleAssignment(role.id, (), None))
    return CatalogSnapshot(
        source="env",
        models=models,
        default_model=default,
        assignments=tuple(assignments),
    )


def document_from(snapshot: CatalogSnapshot, settings: Settings) -> CatalogDocument:
    return CatalogDocument(
        source=snapshot.source,
        provider=settings.llm_provider,
        base_url_configured=bool(settings.llm_base_url),
        api_key_configured=bool(settings.llm_api_key_value()),
        models=[
            CatalogModelOut(id=item.id, display_name=item.display_name) for item in snapshot.models
        ],
        default_model=snapshot.default_model,
        assignments=[
            RoleAssignmentOut(
                role_id=item.role_id,
                models=list(item.models),
                default_model=item.default_model,
            )
            for item in snapshot.assignments
        ],
    )


def _assignments(
    items: list[RoleAssignmentIn],
    catalog: set[str],
) -> tuple[RoleAssignment, ...]:
    if any(item.role_id not in FIXED_ROLE_IDS for item in items):
        raise Unprocessable(ROLE_ID_INVALID)
    role_ids = [item.role_id for item in items]
    if len(role_ids) != len(FIXED_ROLE_IDS) or set(role_ids) != FIXED_ROLE_IDS:
        raise Unprocessable(ROLE_SET_INVALID)
    by_role = {item.role_id: item for item in items}
    built: list[RoleAssignment] = []
    for role_id in ROLE_ORDER:
        item = by_role[role_id]
        chosen: list[str] = []
        seen: set[str] = set()
        for raw in item.models:
            model_id = _model_id(raw, empty=ASSIGNMENT_ID_EMPTY)
            if model_id in seen:
                raise Unprocessable(ASSIGNMENT_DUPLICATE)
            if model_id not in catalog:
                raise Unprocessable(ASSIGNMENT_OUTSIDE)
            seen.add(model_id)
            chosen.append(model_id)
        default_model = _optional_model_id(item.default_model)
        if default_model is not None and default_model not in seen:
            raise Unprocessable(ROLE_DEFAULT_NOT_IN_SET)
        built.append(RoleAssignment(role_id, tuple(chosen), default_model))
    return tuple(built)


def _model_id(value: str, *, empty: str) -> str:
    text = value.strip()
    if not text:
        raise Unprocessable(empty)
    if len(text) > LLM_MODEL_ID_MAX_LENGTH:
        raise Unprocessable(ID_TOO_LONG)
    return text


def _optional_model_id(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) > LLM_MODEL_ID_MAX_LENGTH:
        raise Unprocessable(ID_TOO_LONG)
    return text


def _display_name(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) > LLM_MODEL_ID_MAX_LENGTH:
        raise Unprocessable(NAME_TOO_LONG)
    return text
