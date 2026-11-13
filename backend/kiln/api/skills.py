"""Skill draft, review, and publish outline. Steps are stored and never executed.

Listing and reading a skill require `skill.read`. Creating a draft requires
`skill.use`. Submitting a draft for review requires `skill.submit`. Publishing
an in-review skill requires `skill.publish`. Rejecting an in-review skill back
to a draft also requires `skill.publish` and a reason. Updating a draft
requires the caller to be its author (`skill.use`) or to hold `skill.publish`.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query

from kiln.api.deps import get_store
from kiln.auth import Principal, require_auth, require_permission
from kiln.db.store import PostgresStore
from kiln.domain.skills import (
    Skill,
    SkillCreate,
    SkillList,
    SkillReject,
    SkillScope,
    SkillStatus,
    may_revise_skill,
)
from kiln.errors import Forbidden

router = APIRouter(
    prefix="/skills",
    tags=["Skill"],
    dependencies=[Depends(require_auth)],
)


def _account_id(principal: Principal) -> UUID | None:
    if principal.account is None:
        return None
    return principal.account.id


@router.get(
    "",
    response_model=SkillList,
    summary="列出 skill",
    dependencies=[Depends(require_permission("skill.read"))],
)
def list_skills(
    status: SkillStatus | None = Query(default=None),
    scope: SkillScope | None = Query(default=None),
    principal: Principal = Depends(require_permission("skill.read")),
    store: PostgresStore = Depends(get_store),
) -> SkillList:
    if scope is SkillScope.usable and "skill.use" not in principal.permissions:
        raise Forbidden()
    return SkillList(
        items=store.list_skills(status, scope=scope, account_id=_account_id(principal))
    )


@router.post(
    "",
    response_model=Skill,
    status_code=201,
    summary="创建 skill 草稿",
    dependencies=[Depends(require_permission("skill.use"))],
)
def create_skill(
    payload: SkillCreate,
    principal: Principal = Depends(require_permission("skill.use")),
    store: PostgresStore = Depends(get_store),
) -> Skill:
    return store.create_skill(payload, _account_id(principal))


@router.get(
    "/{skill_id}",
    response_model=Skill,
    summary="查看 skill",
    dependencies=[Depends(require_permission("skill.read"))],
)
def get_skill(skill_id: UUID, store: PostgresStore = Depends(get_store)) -> Skill:
    return store.get_skill(skill_id)


@router.patch(
    "/{skill_id}",
    response_model=Skill,
    summary="更新 skill 草稿",
)
def update_skill(
    skill_id: UUID,
    payload: SkillCreate,
    principal: Principal = Depends(require_auth),
    store: PostgresStore = Depends(get_store),
) -> Skill:
    if "skill.publish" not in principal.permissions and "skill.use" not in principal.permissions:
        raise Forbidden()
    current = store.get_skill(skill_id)
    if not may_revise_skill(principal.permissions, _account_id(principal), current):
        raise Forbidden()
    return store.update_skill(skill_id, payload)


@router.post(
    "/{skill_id}/submit-review",
    response_model=Skill,
    summary="提交审核",
    dependencies=[Depends(require_permission("skill.submit"))],
)
def submit_review(skill_id: UUID, store: PostgresStore = Depends(get_store)) -> Skill:
    return store.submit_skill_review(skill_id)


@router.post(
    "/{skill_id}/publish",
    response_model=Skill,
    summary="发布 skill",
    dependencies=[Depends(require_permission("skill.publish"))],
)
def publish_skill(skill_id: UUID, store: PostgresStore = Depends(get_store)) -> Skill:
    return store.publish_skill(skill_id)


@router.post(
    "/{skill_id}/reject",
    response_model=Skill,
    summary="退回草稿",
)
def reject_skill(
    skill_id: UUID,
    payload: SkillReject,
    principal: Principal = Depends(require_permission("skill.publish")),
    store: PostgresStore = Depends(get_store),
) -> Skill:
    actor_id = principal.account.id if principal.account else None
    return store.reject_skill(skill_id, payload.reason, actor_id)
