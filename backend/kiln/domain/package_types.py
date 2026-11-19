"""Admin-managed build package types and runtime conditions.

The four seed codes stay valid. Any other code is legal once an administrator
adds it to the catalog. Jenkins labels are a single existing label or node
name, not a label expression.
"""

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from kiln.domain.text import has_invisible_control

CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
VERSION_PATTERN = re.compile(r"^[0-9][0-9A-Za-z._+-]{0,31}$")
LABEL_MAX = 40
DESCRIPTION_MAX = 500
JENKINS_LABEL_MAX = 128
CONDITION_LIMIT = 4

SEED_CODES = ("maven", "python", "generic", "frontend")
SEED_LABELS = {
    "maven": "Maven",
    "python": "Python",
    "generic": "通用包",
    "frontend": "前端构建包",
}
GENERIC_CODE = "generic"

RuntimeTool = Literal["jdk", "maven", "node", "python"]
RUNTIME_TOOLS = ("jdk", "maven", "node", "python")

TYPE_MISSING = "构建类型不存在"
TYPE_DISABLED = "该构建类型已停用"
TYPE_DISABLED_DRAFT = "该构建类型已停用，不能绑定到草稿"
TYPE_EXISTS = "构建类型已存在"
TYPE_REFERENCED = "该构建类型已被引用，不能删除，只能停用"
TYPE_MISMATCH = "构建类型必须与该 Skill 的构建类型一致"
CODE_INVALID = "编码须以小写字母开头，只能含小写字母、数字和下划线"


def node_unavailable(type_label: str, jenkins_label: str) -> str:
    return (
        "没有可用的 Jenkins 节点可以运行该构建类型"
        f"（类型：{type_label}，标签：{jenkins_label}）"
    )


def node_probe_failed(type_label: str, jenkins_label: str) -> str:
    return (
        "无法确认是否有 Jenkins 节点可以运行该构建类型"
        f"（类型：{type_label}，标签：{jenkins_label}）"
    )


def illegal_text(value: str) -> bool:
    if has_invisible_control(value):
        return True
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def clean_code(value: str) -> str:
    text = value.strip()
    if CODE_PATTERN.fullmatch(text) is None:
        raise ValueError(CODE_INVALID)
    return text


def clean_label(value: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError("标签不能为空")
    if len(text) > LABEL_MAX:
        raise ValueError("标签过长")
    if illegal_text(text):
        raise ValueError("标签含有非法字符")
    return text


def clean_description(value: str) -> str:
    text = value.strip()
    if len(text) > DESCRIPTION_MAX:
        raise ValueError("说明过长")
    if illegal_text(text):
        raise ValueError("说明含有非法字符")
    return text


def clean_jenkins_label(value: str | None) -> str | None:
    """Return a single label or node name, or None when the job may roam."""

    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) > JENKINS_LABEL_MAX:
        raise ValueError("Jenkins 标签过长")
    if illegal_text(text):
        raise ValueError("Jenkins 标签含有非法字符")
    if "&&" in text or "||" in text:
        raise ValueError("Jenkins 标签不能使用 && 或 ||")
    return text


def require_jenkins_label(value: str) -> str:
    cleaned = clean_jenkins_label(value)
    if cleaned is None:
        raise ValueError("Jenkins 标签不能为空")
    return cleaned


class RuntimeCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: RuntimeTool
    version: str

    @field_validator("version")
    @classmethod
    def clean_version(cls, value: str) -> str:
        text = value.strip()
        if VERSION_PATTERN.fullmatch(text) is None or illegal_text(text):
            raise ValueError("版本格式不正确")
        return text


def clean_conditions(value: list[RuntimeCondition]) -> list[RuntimeCondition]:
    if len(value) > CONDITION_LIMIT:
        raise ValueError("运行时条件过多")
    seen: set[str] = set()
    for item in value:
        if item.tool in seen:
            raise ValueError("每种工具只能有一条版本条件")
        seen.add(item.tool)
    return value


class BuildPackageType(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    label: str
    description: str = ""
    enabled: bool
    runtime_conditions: list[RuntimeCondition] = Field(default_factory=list)
    jenkins_label: str | None = None
    created_at: datetime
    updated_at: datetime


class BuildPackageTypeList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[BuildPackageType]


class _PackageTypeWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    description: str = ""
    enabled: bool = True
    runtime_conditions: list[RuntimeCondition] = Field(
        default_factory=list,
        max_length=CONDITION_LIMIT,
    )
    jenkins_label: str | None = None

    @field_validator("label")
    @classmethod
    def _label(cls, value: str) -> str:
        return clean_label(value)

    @field_validator("description")
    @classmethod
    def _description(cls, value: str) -> str:
        return clean_description(value)

    @field_validator("jenkins_label")
    @classmethod
    def _jenkins_label(cls, value: str | None) -> str | None:
        return clean_jenkins_label(value)

    @field_validator("runtime_conditions")
    @classmethod
    def _conditions(cls, value: list[RuntimeCondition]) -> list[RuntimeCondition]:
        return clean_conditions(value)


class BuildPackageTypeCreate(_PackageTypeWrite):
    code: str

    @field_validator("code")
    @classmethod
    def _code(cls, value: str) -> str:
        return clean_code(value)


class BuildPackageTypeUpdate(_PackageTypeWrite):
    pass
