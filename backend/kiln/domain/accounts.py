"""Local account models. Passwords, login, and identity federation are not implemented."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from kiln.domain.rbac import ROLE_INDEX


class AccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,31}$")
    display_name: str = Field(min_length=1, max_length=80)
    email: str | None = None
    role_ids: list[str] = Field(default_factory=lambda: ["reader"])

    @field_validator("display_name")
    @classmethod
    def strip_display_name(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("显示名不能为空")
        if any(ord(char) < 32 for char in text):
            raise ValueError("显示名含有非法字符")
        return text

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if text == "":
            return None
        if "@" not in text or text.startswith("@") or text.endswith("@") or " " in text:
            raise ValueError("邮箱格式不正确")
        return text

    @field_validator("role_ids")
    @classmethod
    def known_roles(cls, value: list[str]) -> list[str]:
        unknown = [item for item in value if item not in ROLE_INDEX]
        if unknown:
            raise ValueError("未知角色：" + "、".join(unknown))
        if len(set(value)) != len(value):
            raise ValueError("角色重复")
        return value


class Account(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    username: str
    display_name: str
    email: str | None
    status: Literal["active", "disabled"]
    role_ids: list[str]
    created_at: datetime


class AccountList(BaseModel):
    persistence: Literal["memory"] = "memory"
    items: list[Account]
