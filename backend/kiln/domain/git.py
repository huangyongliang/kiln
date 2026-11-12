"""Replaceable git-source records.

`gitea` is the lab adapter. `gitlab` is the production code source. The base
URL is whatever the process was configured with; this module does not name a
production host.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

GitProviderName = Literal["gitea", "gitlab", "off"]
GitRole = Literal["lab", "production", "unconfigured"]


class GitRepository(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner: str
    name: str
    full_name: str
    default_branch: str


class GitRepositoryList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["gitea", "gitlab"]
    role: Literal["lab", "production"]
    items: list[GitRepository]


class ResolvedCommit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["gitea", "gitlab"]
    role: Literal["lab", "production"]
    owner: str
    name: str
    ref: str
    sha: str


class GitSourceInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: GitProviderName
    role: GitRole
    production_provider: Literal["gitlab"] = "gitlab"
    configured: bool
    base_url: str | None
