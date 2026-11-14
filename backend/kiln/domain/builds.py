"""Product build records.

A product build checks out a git commit on Jenkins, runs that commit's
`kiln-build.sh`, and stores `kiln-dist/artifact.bin` in platform-owned local
storage. It does not deploy to a runtime and does not execute skill steps.
A new build requires a published skill. `toolchain`, `environment`,
`output_spec`, and `inputs` stay documentary.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from kiln.domain.artifacts import VERSION_PATTERN, PackageType
from kiln.domain.jobs import JobStatus
from kiln.errors import BadRequest
from kiln.git.refs import parse_repository

ContentConflict = Literal["foreign_bytes", "foreign_version"]


class ProductBuildCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: UUID
    repository: str = Field(min_length=3, max_length=400)
    ref: str = Field(min_length=1, max_length=255)
    artifact_name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{1,127}$")
    version: str = Field(pattern=VERSION_PATTERN)
    package_type: PackageType
    name: str = Field(default="", pattern=r"^$|^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class ProductBuild(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    skill_id: UUID | None = None
    skill_name: str | None = None
    jenkins_job_name: str
    repository: str
    ref: str
    commit_sha: str
    status: JobStatus
    build_number: int | None
    artifact_id: UUID
    version: str
    package_type: PackageType
    content_available: bool
    content_conflict: ContentConflict | None = None
    created_at: datetime
    updated_at: datetime
    requested_by: UUID | None = None
    deploys_to_runtime: Literal[False] = False


class ProductBuildList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    persistence: Literal["postgres"] = "postgres"
    items: list[ProductBuild]


class BuildLog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    build_id: UUID
    build_number: int
    text: str
    truncated: bool


def repository_in_skill_scope(repository: str, allowed: list[str]) -> bool:
    """Whether `repository` may be built under this skill's allow list.

    An empty list allows any repository the code source can resolve. When both
    the request and an entry parse as ``owner/name``, compare the normalized
    form. An entry that does not parse matches the trimmed raw input or that
    normalized ``owner/name`` exactly.
    """

    if not allowed:
        return True
    raw = repository.strip()
    owner, name = parse_repository(raw)
    normalized = f"{owner}/{name}"
    for entry in allowed:
        item = entry.strip()
        try:
            entry_owner, entry_name = parse_repository(item)
        except BadRequest:
            if item == raw or item == normalized:
                return True
            continue
        if f"{entry_owner}/{entry_name}" == normalized:
            return True
    return False
