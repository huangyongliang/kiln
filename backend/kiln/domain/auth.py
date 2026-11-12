"""Login and 用户信息同步 responses. Passwords are not included."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from kiln.domain.accounts import Account


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=128)


class SessionLogin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str
    expires_at: datetime
    account: Account


class SyncStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    configured: bool


class SessionPrincipal(BaseModel):
    """Current principal. No token, password, session hash, or expiry."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["user", "machine"]
    account: Account | None
    permissions: list[str]
