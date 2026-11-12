"""Process-local stores for the phase-1 API outline.

Records disappear when the process stops. PostgreSQL is the intended metadata
store and is not wired in this skeleton.
"""

import threading
from datetime import timedelta
from uuid import UUID, uuid4

from kiln.clock import utcnow
from kiln.domain.accounts import Account, AccountCreate
from kiln.domain.artifacts import (
    PACKAGE_LABELS,
    Artifact,
    ArtifactCreate,
    ArtifactVersion,
    ArtifactVersionCreate,
    DownloadReservationCreate,
    Reservation,
    RetrieveResult,
)
from kiln.domain.skills import Skill, SkillCreate, SkillStatus
from kiln.errors import Conflict, Gone, NotFound
from kiln.paths import retrieve_path


class MemoryStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._accounts: list[Account] = []
        self._artifacts: list[Artifact] = []
        self._reservations: dict[UUID, Reservation] = {}
        self._skills: list[Skill] = []

    def list_accounts(self) -> list[Account]:
        with self._lock:
            return [item.model_copy(deep=True) for item in self._accounts]

    def get_account(self, account_id: UUID) -> Account:
        with self._lock:
            return self._require_account(account_id).model_copy(deep=True)

    def create_account(self, payload: AccountCreate) -> Account:
        with self._lock:
            if any(item.username == payload.username for item in self._accounts):
                raise Conflict("用户名已存在")
            account = Account(
                id=uuid4(),
                username=payload.username,
                display_name=payload.display_name,
                email=payload.email,
                status="active",
                role_ids=list(payload.role_ids),
                created_at=utcnow(),
            )
            self._accounts.append(account)
            return account.model_copy(deep=True)

    def list_artifacts(self) -> list[Artifact]:
        with self._lock:
            return [item.model_copy(deep=True) for item in self._artifacts]

    def get_artifact(self, artifact_id: UUID) -> Artifact:
        with self._lock:
            return self._require_artifact(artifact_id).model_copy(deep=True)

    def create_artifact(self, payload: ArtifactCreate) -> Artifact:
        with self._lock:
            duplicate = any(
                item.group == payload.group and item.name == payload.name
                for item in self._artifacts
            )
            if duplicate:
                raise Conflict("同组内制品名称已存在")
            artifact = Artifact(
                id=uuid4(),
                name=payload.name,
                group=payload.group,
                description=payload.description,
                package_type=payload.package_type,
                package_type_label=PACKAGE_LABELS[payload.package_type],
                versions=[],
                created_at=utcnow(),
            )
            self._artifacts.append(artifact)
            return artifact.model_copy(deep=True)

    def add_version(self, artifact_id: UUID, payload: ArtifactVersionCreate) -> ArtifactVersion:
        with self._lock:
            artifact = self._require_artifact(artifact_id)
            if any(item.version == payload.version for item in artifact.versions):
                raise Conflict("版本已存在且不可变")
            version = ArtifactVersion(
                version=payload.version,
                checksum=payload.checksum,
                size_bytes=payload.size_bytes,
                source=payload.source,
                created_at=utcnow(),
            )
            artifact.versions.append(version)
            return version.model_copy(deep=True)

    def reserve_download(
        self,
        artifact_id: UUID,
        version: str,
        payload: DownloadReservationCreate,
    ) -> Reservation:
        with self._lock:
            artifact = self._require_artifact(artifact_id)
            matched = next((item for item in artifact.versions if item.version == version), None)
            if matched is None:
                raise NotFound("版本不存在")
            created_at = utcnow()
            reservation_id = uuid4()
            reservation = Reservation(
                id=reservation_id,
                artifact_id=artifact.id,
                artifact_name=artifact.name,
                version=matched.version,
                package_type=artifact.package_type,
                package_type_label=artifact.package_type_label,
                checksum=matched.checksum,
                requester=payload.requester,
                created_at=created_at,
                expires_at=created_at + timedelta(seconds=payload.ttl_seconds),
                retrieve_path=retrieve_path(reservation_id),
            )
            self._reservations[reservation_id] = reservation
            return reservation.model_copy(deep=True)

    def get_retrieve(self, reservation_id: UUID) -> RetrieveResult:
        with self._lock:
            reservation = self._reservations.get(reservation_id)
            if reservation is None:
                raise NotFound("取包预约不存在")
            if reservation.expires_at <= utcnow():
                raise Gone("取包预约已过期")
            return RetrieveResult(
                reservation_id=reservation.id,
                artifact_id=reservation.artifact_id,
                name=reservation.artifact_name,
                version=reservation.version,
                package_type=reservation.package_type,
                package_type_label=reservation.package_type_label,
                checksum=reservation.checksum,
                detail="一期骨架只登记取包预约，不提供制品字节。",
            )

    def list_skills(self, status: SkillStatus | None) -> list[Skill]:
        with self._lock:
            items = self._skills
            if status is not None:
                items = [item for item in items if item.status == status]
            return [item.model_copy(deep=True) for item in items]

    def get_skill(self, skill_id: UUID) -> Skill:
        with self._lock:
            return self._require_skill(skill_id).model_copy(deep=True)

    def create_skill(self, payload: SkillCreate) -> Skill:
        with self._lock:
            duplicate = any(
                item.name == payload.name and item.version == payload.version
                for item in self._skills
            )
            if duplicate:
                raise Conflict("同名同版本的 skill 已存在")
            now = utcnow()
            skill = Skill(
                id=uuid4(),
                name=payload.name,
                version=payload.version,
                status=SkillStatus.draft,
                description=payload.description,
                inputs=list(payload.inputs),
                toolchain=payload.toolchain,
                environment=payload.environment,
                allowed_repositories=list(payload.allowed_repositories),
                allowed_dependencies=list(payload.allowed_dependencies),
                steps=list(payload.steps),
                output_spec=payload.output_spec,
                created_at=now,
                updated_at=now,
            )
            self._skills.append(skill)
            return skill.model_copy(deep=True)

    def submit_skill_review(self, skill_id: UUID) -> Skill:
        with self._lock:
            skill = self._require_skill(skill_id)
            if skill.status != SkillStatus.draft:
                raise Conflict("只有草稿可以提交审核")
            skill.status = SkillStatus.in_review
            skill.updated_at = utcnow()
            return skill.model_copy(deep=True)

    def publish_skill(self, skill_id: UUID) -> Skill:
        with self._lock:
            skill = self._require_skill(skill_id)
            if skill.status != SkillStatus.in_review:
                raise Conflict("只有审核中的 skill 可以发布")
            skill.status = SkillStatus.published
            skill.updated_at = utcnow()
            return skill.model_copy(deep=True)

    def _require_account(self, account_id: UUID) -> Account:
        found = next((item for item in self._accounts if item.id == account_id), None)
        if found is None:
            raise NotFound("账号不存在")
        return found

    def _require_artifact(self, artifact_id: UUID) -> Artifact:
        found = next((item for item in self._artifacts if item.id == artifact_id), None)
        if found is None:
            raise NotFound("制品不存在")
        return found

    def _require_skill(self, skill_id: UUID) -> Skill:
        found = next((item for item in self._skills if item.id == skill_id), None)
        if found is None:
            raise NotFound("skill 不存在")
        return found
