"""Skill draft and publish models.

Steps are declarative data. This skeleton does not execute them and does not
run shell commands.
"""

from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator


class SkillStatus(str, Enum):
    draft = "draft"
    in_review = "in_review"
    published = "published"


STATUS_LABELS = {
    SkillStatus.draft: "草稿",
    SkillStatus.in_review: "审核中",
    SkillStatus.published: "已发布",
}


class SkillStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["checkout", "compile", "package", "archive"]
    description: str = Field(default="", max_length=500)


class SkillInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    description: str = Field(default="", max_length=300)
    required: bool = True


class SkillCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    version: str = Field(default="0.1.0", pattern=r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
    description: str = Field(default="", max_length=2000)
    inputs: list[SkillInput] = Field(default_factory=list, max_length=32)
    toolchain: str | None = Field(default=None, max_length=200)
    environment: str | None = Field(default=None, max_length=200)
    allowed_repositories: list[str] = Field(default_factory=list, max_length=50)
    allowed_dependencies: list[str] = Field(default_factory=list, max_length=50)
    steps: list[SkillStep] = Field(default_factory=list, max_length=20)
    output_spec: str | None = Field(default=None, max_length=500)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        text = value.strip()
        if len(text) < 2:
            raise ValueError("名称至少 2 个字符")
        if any(ord(char) < 32 for char in text):
            raise ValueError("名称含有非法字符")
        return text

    @field_validator("toolchain", "environment", "output_spec")
    @classmethod
    def empty_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None

    @field_validator("allowed_repositories", "allowed_dependencies")
    @classmethod
    def clean_scope(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("范围条目不能为空")
        if any(len(item) > 200 for item in cleaned):
            raise ValueError("范围条目过长")
        return cleaned


class Skill(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    version: str
    status: SkillStatus
    description: str
    inputs: list[SkillInput]
    toolchain: str | None
    environment: str | None
    allowed_repositories: list[str]
    allowed_dependencies: list[str]
    steps: list[SkillStep]
    output_spec: str | None
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def status_label(self) -> str:
        return STATUS_LABELS[self.status]


class SkillList(BaseModel):
    persistence: Literal["memory"] = "memory"
    items: list[Skill]
