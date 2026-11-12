"""Select the git adapter from configuration.

Production code source: GitLab (`GIT_PROVIDER=gitlab`).
Lab stand-in: Gitea (`GIT_PROVIDER=gitea`).
Neither choice implies a host. The base URL comes from settings.
"""

from typing import Protocol

import httpx

from kiln.config import Settings
from kiln.domain.git import GitRepository, GitRole, ResolvedCommit
from kiln.git.gitea import GiteaSource
from kiln.git.gitlab import GitLabSource

PRODUCTION_GIT_PROVIDER = "gitlab"
LAB_GIT_PROVIDER = "gitea"


class GitSource(Protocol):
    provider: str
    role: str

    def list_repositories(self) -> list[GitRepository]: ...

    def resolve_ref(self, owner: str, name: str, ref: str) -> ResolvedCommit: ...


def role_for_provider(provider: str) -> GitRole:
    if provider == LAB_GIT_PROVIDER:
        return "lab"
    if provider == PRODUCTION_GIT_PROVIDER:
        return "production"
    return "unconfigured"


def build_git_source(settings: Settings, client: httpx.Client) -> GitSource | None:
    if not settings.git_configured:
        return None
    if settings.git_provider == LAB_GIT_PROVIDER:
        return GiteaSource(settings.git_base_url, settings.git_token_value(), client)
    if settings.git_provider == PRODUCTION_GIT_PROVIDER:
        return GitLabSource(settings.git_base_url, settings.git_token_value(), client)
    return None
