"""Product build records.

A product build checks out a git commit on Jenkins, runs that commit's
`kiln-build.sh`, and stores `kiln-dist/artifact.bin` in platform-owned local
storage. It does not deploy to a runtime.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from kiln.domain.artifacts import VERSION_PATTERN, PackageType
from kiln.domain.jobs import JobStatus


class ProductBuildCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    created_at: datetime
    updated_at: datetime
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
