"""Local RBAC catalog.

The catalog is who may manage accounts, use, submit, or publish skills, upload
or download artifacts, read executor, job, build, and git-source state, and
start a job or product build on a compat executor, plus which in-platform
agent abilities can be granted. Routes that match one of these codes check
the signed-in user's roles. `KILN_API_TOKEN` is a machine credential and has
every code. Optional company user-system sync does not copy groups or roles
into this catalog.
"""

from pydantic import BaseModel, ConfigDict

_READER = (
    "account.read",
    "skill.read",
    "artifact.read",
)
_BUILDER = _READER + (
    "skill.use",
    "skill.submit",
    "artifact.upload",
    "artifact.download",
    "agent.qa",
    "agent.draft_skill",
    "agent.build_artifact",
    "executor.read",
    "executor.run",
    "git.read",
)
_PUBLISHER = _BUILDER + (
    "skill.publish",
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
    Permission(code="skill.submit", description="提交 skill 审核"),
    Permission(code="skill.publish", description="审核并发布 skill"),
    Permission(code="artifact.read", description="查看制品目录"),
    Permission(code="artifact.upload", description="登记制品版本"),
    Permission(code="artifact.download", description="预约并取用制品"),
    Permission(code="agent.qa", description="使用平台内问答"),
    Permission(code="agent.draft_skill", description="使用对话起草 skill"),
    Permission(code="agent.build_artifact", description="使用对话制作制品"),
    Permission(
        code="executor.read",
        description="查看兼容执行器状态、任务与产品构建及其日志",
    ),
    Permission(code="executor.run", description="在兼容执行器上发起任务或产品构建"),
    Permission(code="git.read", description="查看代码源适配器、仓库列表并解析引用"),
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
        description=(
            "使用已发布 skill、起草并提交审核、登记制品，"
            "在兼容执行器上发起任务或产品构建，并使用问答与制作制品"
        ),
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
ALL_PERMISSIONS = frozenset(item.code for item in PERMISSIONS)


def permissions_for(role_ids: list[str]) -> frozenset[str]:
    codes: set[str] = set()
    for role_id in role_ids:
        role = ROLE_INDEX.get(role_id)
        if role is not None:
            codes.update(role.permissions)
    return frozenset(codes)


for _role in ROLES:
    unknown = set(_role.permissions) - _PERMISSION_CODES
    if unknown:
        raise RuntimeError(f"role {_role.id} references unknown permissions: {unknown}")
