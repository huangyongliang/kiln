"""Local RBAC catalog.

The catalog is the phase-1 outline of who may use skills, publish skills, and
download artifacts, plus which in-platform agent abilities can be granted.
Requests are not authorized against it yet.
"""

from pydantic import BaseModel, ConfigDict

_READER = (
    "account.read",
    "skill.read",
    "artifact.read",
)
_BUILDER = _READER + (
    "skill.use",
    "artifact.upload",
    "artifact.download",
    "agent.qa",
    "agent.build_artifact",
)
_PUBLISHER = _BUILDER + (
    "skill.publish",
    "agent.draft_skill",
)


class Permission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    description: str


class Role(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str
    permissions: list[str]


class PermissionList(BaseModel):
    items: list[Permission]


class RoleList(BaseModel):
    items: list[Role]


PERMISSIONS: tuple[Permission, ...] = (
    Permission(code="account.read", description="查看本地账号"),
    Permission(code="account.manage", description="管理本地账号与角色分配"),
    Permission(code="skill.read", description="查看 skill"),
    Permission(code="skill.use", description="使用已发布的 skill"),
    Permission(code="skill.publish", description="审核并发布 skill"),
    Permission(code="artifact.read", description="查看制品目录"),
    Permission(code="artifact.upload", description="登记制品版本"),
    Permission(code="artifact.download", description="预约并取用制品"),
    Permission(code="agent.qa", description="使用平台内问答"),
    Permission(code="agent.draft_skill", description="使用对话起草 skill"),
    Permission(code="agent.build_artifact", description="使用对话制作制品"),
)

_PERMISSION_CODES = {item.code for item in PERMISSIONS}

ROLES: tuple[Role, ...] = (
    Role(
        id="reader",
        name="只读",
        description="查看账号、skill 与制品",
        permissions=list(_READER),
    ),
    Role(
        id="builder",
        name="构建者",
        description="使用已发布 skill、登记制品，并使用问答与制作制品",
        permissions=list(_BUILDER),
    ),
    Role(
        id="publisher",
        name="发布者",
        description="在构建者能力上增加 skill 发布与起草",
        permissions=list(_PUBLISHER),
    ),
    Role(
        id="admin",
        name="管理员",
        description="全部已声明的本地权限",
        permissions=[item.code for item in PERMISSIONS],
    ),
)

ROLE_INDEX = {role.id: role for role in ROLES}

for _role in ROLES:
    unknown = set(_role.permissions) - _PERMISSION_CODES
    if unknown:
        raise RuntimeError(f"role {_role.id} references unknown permissions: {unknown}")
