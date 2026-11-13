"""Skill draft and publish models.

Steps are declarative data. This skeleton does not execute them and does not
run shell commands.
"""

import unicodedata
from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from kiln.domain.text import has_invisible_control


class SkillStatus(str, Enum):
    draft = "draft"
    in_review = "in_review"
    published = "published"


class SkillScope(str, Enum):
    """Optional list filter. Omitting it keeps the full catalog."""

    mine = "mine"
    usable = "usable"


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
        if any(ord(char) < 32 for char in text) or has_invisible_control(text):
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


class SkillReject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("退回原因不能为空")
        if len(text) > 500:
            raise ValueError("退回原因过长")
        if has_invisible_control(text) or any(
            char not in "\n\t" and unicodedata.category(char) == "Cc" for char in text
        ):
            raise ValueError("退回原因含有非法字符")
        return text


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
    created_by: UUID | None = None
    review_note: str | None = None
    rejected_at: datetime | None = None
    rejected_by: UUID | None = None

    @computed_field
    @property
    def status_label(self) -> str:
        return STATUS_LABELS[self.status]


class SkillList(BaseModel):
    persistence: Literal["postgres"] = "postgres"
    items: list[Skill]


def may_revise_skill(
    permissions: frozenset[str] | set[str],
    account_id: UUID | None,
    skill: Skill,
) -> bool:
    """A publisher may revise any draft. A user may revise their own draft.

    ``skill.use`` plus a matching ``created_by`` is the owner path. Historical
    rows with a null author are not owned by anyone, so only ``skill.publish``
    can revise them. Status is checked separately: this does not allow
    in-review or published skills to be written.
    """

    if "skill.publish" in permissions:
        return True
    return (
        "skill.use" in permissions
        and account_id is not None
        and skill.created_by == account_id
    )
