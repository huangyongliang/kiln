"""Skill draft, review, and publish outline. Steps are stored and never executed.

Listing and reading a skill require `skill.read`. Creating a draft requires
`skill.use`. Submitting a draft for review requires `skill.submit`. Publishing
requires `skill.publish`.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query

from kiln.api.deps import get_store
from kiln.auth import require_auth, require_permission
from kiln.db.store import PostgresStore
from kiln.domain.skills import Skill, SkillCreate, SkillList, SkillStatus

router = APIRouter(
    prefix="/skills",
    tags=["Skill"],
    dependencies=[Depends(require_auth)],
)


@router.get(
    "",
    response_model=SkillList,
    summary="列出 skill",
    dependencies=[Depends(require_permission("skill.read"))],
)
def list_skills(
    status: SkillStatus | None = Query(default=None),
    store: PostgresStore = Depends(get_store),
) -> SkillList:
    return SkillList(items=store.list_skills(status))


@router.post(
    "",
    response_model=Skill,
    status_code=201,
    summary="创建 skill 草稿",
    dependencies=[Depends(require_permission("skill.use"))],
)
def create_skill(payload: SkillCreate, store: PostgresStore = Depends(get_store)) -> Skill:
    return store.create_skill(payload)


@router.get(
    "/{skill_id}",
    response_model=Skill,
    summary="查看 skill",
    dependencies=[Depends(require_permission("skill.read"))],
)
def get_skill(skill_id: UUID, store: PostgresStore = Depends(get_store)) -> Skill:
    return store.get_skill(skill_id)


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
