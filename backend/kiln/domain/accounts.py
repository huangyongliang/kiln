"""Local account models.

A password is optional. Accounts created by 用户信息同步 may have none.
The plaintext is accepted on create and is not part of the stored account.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from kiln.domain.rbac import ROLE_INDEX

_USERNAME = re.compile(r"^[a-z][a-z0-9_-]{2,31}$")


class AccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,31}$")
    display_name: str = Field(min_length=1, max_length=80)
    email: str | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)
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
    persistence: Literal["postgres"] = "postgres"
    items: list[Account]


@dataclass(frozen=True)
class SyncLink:
    """Result of linking an external subject to a kiln account.

    `denial` is set when no session may be created. A new account is not
    inserted when employment is inactive.
    """

    account: Account | None
    denial: Literal["employment", "disabled"] | None = None


def username_seed(preferred: str | None, email: str | None, subject: str) -> str:
    """Build a local username from synced identity claims.

    The seed is only a kiln username. It does not copy external roles.
    """

    raw = (preferred or "").strip().lower()
    if not raw and email and "@" in email:
        raw = email.split("@", 1)[0].strip().lower()
    if not raw:
        raw = "user"
    cleaned = re.sub(r"[^a-z0-9_-]", "_", raw)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_-")
    if not cleaned or not cleaned[0].isalpha():
        cleaned = f"u{cleaned}"
    if len(cleaned) < 3:
        cleaned = f"{cleaned}user"
    cleaned = cleaned[:32]
    if _USERNAME.fullmatch(cleaned):
        return cleaned
    digest = hashlib.sha256(subject.encode("utf-8")).hexdigest()[:6]
    return f"user_{digest}"
