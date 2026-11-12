"""Git source HTTP API.

Listing repositories and resolving a ref require the local API token.
The adapter is selected by GIT_PROVIDER: gitea for the lab, gitlab for
production. The base URL is not hard-coded.
"""

import re

from fastapi import APIRouter, Depends, Query

from kiln.api.deps import get_git_source, get_settings
from kiln.auth import require_api_token
from kiln.config import Settings
from kiln.domain.git import GitRepositoryList, GitSourceInfo, ResolvedCommit
from kiln.errors import BadRequest
from kiln.git.source import GitSource, role_for_provider

router = APIRouter(prefix="/git", tags=["代码源"], dependencies=[Depends(require_api_token)])

_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")


@router.get("/source", response_model=GitSourceInfo, summary="代码源适配器")
def git_source_info(settings: Settings = Depends(get_settings)) -> GitSourceInfo:
    provider = settings.git_provider if settings.git_configured else "off"
    return GitSourceInfo(
        provider=provider,
        role=role_for_provider(provider),
        production_provider="gitlab",
        configured=settings.git_configured,
        base_url=settings.git_base_url or None,
    )


@router.get("/repositories", response_model=GitRepositoryList, summary="列出代码仓库")
def list_repositories(source: GitSource = Depends(get_git_source)) -> GitRepositoryList:
    return GitRepositoryList(
        provider=_provider(source),
        role=_role(source),
        items=source.list_repositories(),
    )


@router.get("/commits", response_model=ResolvedCommit, summary="解析仓库引用")
def resolve_commit(
    repository: str = Query(min_length=3, max_length=400),
    ref: str = Query(min_length=1, max_length=255),
    source: GitSource = Depends(get_git_source),
) -> ResolvedCommit:
    owner, name = _repository(repository)
    resolved_ref = _ref(ref)
    return source.resolve_ref(owner, name, resolved_ref)


def _repository(value: str) -> tuple[str, str]:
    parts = value.strip().split("/")
    if len(parts) < 2 or any(_SEGMENT.fullmatch(part) is None for part in parts):
        raise BadRequest("仓库必须是 owner/name")
    name = parts[-1]
    owner = "/".join(parts[:-1])
    return owner, name


def _ref(value: str) -> str:
    text = value.strip()
    if (
        _REF.fullmatch(text) is None
        or text.startswith("/")
        or text.endswith("/")
        or ".." in text
        or "//" in text
    ):
        raise BadRequest("引用无效")
    return text


def _provider(source: GitSource) -> str:
    if source.provider not in {"gitea", "gitlab"}:
        raise BadRequest("代码源适配器无效")
    return source.provider


def _role(source: GitSource) -> str:
    if source.role not in {"lab", "production"}:
        raise BadRequest("代码源角色无效")
    return source.role
