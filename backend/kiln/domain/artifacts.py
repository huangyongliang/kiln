"""Artifact catalog models.

Versions are immutable and carry a checksum. Bytes live in platform-owned
local storage. Download reservations point at a stable retrieve path.
`content_available` is true only after those bytes are stored.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

VERSION_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$"


class PackageType(str, Enum):
    maven = "maven"
    python = "python"
    generic = "generic"
    frontend = "frontend"


PACKAGE_LABELS = {
    PackageType.maven: "Maven",
    PackageType.python: "Python",
    PackageType.generic: "通用包",
    PackageType.frontend: "前端构建包",
}


class ArtifactSource(str, Enum):
    build = "build"
    upload = "upload"


# HTTP uploads from a signed-in account. Not an environment variable.
# Machine tokens and in-process build collection keep KILN_ARTIFACT_MAX_BYTES.
SESSION_UPLOAD_MAX_BYTES = 1_048_576

BUILD_OWNED_WRITE_DETAIL = "该制品由产品构建产出，不接受手工登记或上传"


class Checksum(BaseModel):
    model_config = ConfigDict(extra="forbid")

    algorithm: Literal["sha256", "sha512"]
    value: str

    @model_validator(mode="after")
    def hex_digest(self) -> "Checksum":
        length = 64 if self.algorithm == "sha256" else 128
        digits = "0123456789abcdefABCDEF"
        if len(self.value) != length or any(char not in digits for char in self.value):
            raise ValueError(f"{self.algorithm} 需要 {length} 位十六进制字符串")
        self.value = self.value.lower()
        return self


class ArtifactCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{1,127}$")
    group: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    description: str = Field(default="", max_length=2000)
    package_type: PackageType


class ArtifactVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(pattern=VERSION_PATTERN)
    checksum: Checksum
    size_bytes: int | None = Field(default=None, ge=0)
    source: ArtifactSource = ArtifactSource.upload

    @model_validator(mode="after")
    def http_source_is_upload(self) -> "ArtifactVersionCreate":
        if self.source is not ArtifactSource.upload:
            raise ValueError("来源只能是 upload")
        return self


class ArtifactVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    checksum: Checksum
    size_bytes: int | None
    source: ArtifactSource
    content_available: bool
    created_by: UUID | None = None
    created_at: datetime


class Artifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    group: str | None
    description: str
    package_type: PackageType
    package_type_label: str
    versions: list[ArtifactVersion]
    build_owned: bool = False
    created_at: datetime


class ArtifactList(BaseModel):
    persistence: Literal["postgres"] = "postgres"
    items: list[Artifact]


class DownloadReservationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requester: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{2,31}$")
    ttl_seconds: int = Field(default=900, ge=60, le=86400)


class Reservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    artifact_id: UUID
    artifact_name: str
    version: str
    package_type: PackageType
    package_type_label: str
    checksum: Checksum
    requester: str
    account_id: UUID | None = None
    created_at: datetime
    expires_at: datetime
    retrieve_path: str


class RetrieveResult(BaseModel):
    """Retrieve metadata. Bytes are downloaded from `content_path` when stored."""

    model_config = ConfigDict(extra="forbid")

    reservation_id: UUID
    artifact_id: UUID
    name: str
    version: str
    package_type: PackageType
    package_type_label: str
    checksum: Checksum
    status: Literal["reserved"] = "reserved"
    content_available: bool
    content_path: str | None = None
    detail: str


@dataclass(frozen=True)
class RetrievedBytes:
    data: bytes
    name: str
    version: str
    algorithm: str
    digest: str
