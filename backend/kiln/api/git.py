"""Git source HTTP API.

Listing repositories and resolving a ref require `git.read`. The adapter is
selected by GIT_PROVIDER: gitea for the lab, gitlab for production. The base
URL is not hard-coded.
"""

from fastapi import APIRouter, Depends, Query

from kiln.api.deps import get_git_source, get_settings
from kiln.auth import require_auth, require_permission
from kiln.config import Settings
from kiln.domain.git import GitRepositoryList, GitSourceInfo, ResolvedCommit
from kiln.errors import BadRequest
from kiln.git.refs import parse_ref, parse_repository
from kiln.git.source import GitSource, role_for_provider

router = APIRouter(prefix="/git", tags=["代码源"], dependencies=[Depends(require_auth)])


@router.get(
    "/source",
    response_model=GitSourceInfo,
    summary="代码源适配器",
    dependencies=[Depends(require_permission("git.read"))],
)
def git_source_info(settings: Settings = Depends(get_settings)) -> GitSourceInfo:
    provider = settings.git_provider if settings.git_configured else "off"
    return GitSourceInfo(
        provider=provider,
        role=role_for_provider(provider),
        production_provider="gitlab",
        configured=settings.git_configured,
        base_url=settings.git_base_url or None,
    )


@router.get(
    "/repositories",
    response_model=GitRepositoryList,
    summary="列出代码仓库",
    dependencies=[Depends(require_permission("git.read"))],
)
def list_repositories(source: GitSource = Depends(get_git_source)) -> GitRepositoryList:
    return GitRepositoryList(
        provider=_provider(source),
        role=_role(source),
        items=source.list_repositories(),
    )


@router.get(
    "/commits",
    response_model=ResolvedCommit,
    summary="解析仓库引用",
    dependencies=[Depends(require_permission("git.read"))],
)
def resolve_commit(
    repository: str = Query(min_length=3, max_length=400),
    ref: str = Query(min_length=1, max_length=255),
    source: GitSource = Depends(get_git_source),
) -> ResolvedCommit:
    owner, name = parse_repository(repository)
    resolved_ref = parse_ref(ref)
    return source.resolve_ref(owner, name, resolved_ref)


def _provider(source: GitSource) -> str:
    if source.provider not in {"gitea", "gitlab"}:
        raise BadRequest("代码源适配器无效")
    return source.provider


def _role(source: GitSource) -> str:
    if source.role not in {"lab", "production"}:
        raise BadRequest("代码源角色无效")
    return source.role
