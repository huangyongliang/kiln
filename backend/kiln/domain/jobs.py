"""Jenkins compat job records.

kiln stores the job and reads status and logs from Jenkins. It does not deploy
the job to a runtime.
"""

from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    success = "success"
    failure = "failure"
    unstable = "unstable"
    aborted = "aborted"
    not_built = "not_built"
    unknown = "unknown"


TERMINAL_STATUSES = {
    JobStatus.success,
    JobStatus.failure,
    JobStatus.unstable,
    JobStatus.aborted,
    JobStatus.not_built,
}


class JobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="", pattern=r"^$|^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    marker: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")


class Job(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    jenkins_job_name: str
    marker: str
    status: JobStatus
    build_number: int | None
    created_at: datetime
    updated_at: datetime
    requested_by: UUID | None = None
    deploys_to_runtime: Literal[False] = False


class JobList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    persistence: Literal["postgres"] = "postgres"
    items: list[Job]


class JobLog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    build_number: int
    text: str
    truncated: bool


class ExecutorInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["jenkins", "off"]
    configured: bool
    base_url: str | None
    deploys_to_runtime: Literal[False] = False
