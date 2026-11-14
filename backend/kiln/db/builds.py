"""PostgreSQL store for product builds.

Rows keep the commit and the catalog link. Artifact bytes stay in the
platform blob store. Git tokens are not written here.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from kiln import clock
from kiln.db.models import ArtifactRow, ArtifactVersionRow, ProductBuildRow, SkillRow
from kiln.db.store import _commit_unique
from kiln.domain.artifacts import PackageType
from kiln.domain.builds import ContentConflict, ProductBuild
from kiln.domain.jobs import JobStatus
from kiln.errors import Conflict, NotFound


class StoredBuild:
    def __init__(
        self,
        *,
        id: UUID,
        name: str,
        jenkins_job_name: str,
        repository: str,
        ref: str,
        commit_sha: str,
        artifact_id: UUID,
        version: str,
        package_type: str,
        queue_url: str | None,
        build_number: int | None,
        status: JobStatus,
        content_stored: bool,
        skill_id: UUID | None,
        skill_name: str | None,
        requested_by: UUID | None,
        created_at: datetime,
        updated_at: datetime,
    ) -> None:
        self.id = id
        self.name = name
        self.jenkins_job_name = jenkins_job_name
        self.repository = repository
        self.ref = ref
        self.commit_sha = commit_sha
        self.artifact_id = artifact_id
        self.version = version
        self.package_type = package_type
        self.queue_url = queue_url
        self.build_number = build_number
        self.status = status
        self.content_stored = content_stored
        self.skill_id = skill_id
        self.skill_name = skill_name
        self.requested_by = requested_by
        self.created_at = created_at
        self.updated_at = updated_at

    def public(
        self,
        *,
        content_available: bool,
        content_conflict: ContentConflict | None = None,
    ) -> ProductBuild:
        return ProductBuild(
            id=self.id,
            name=self.name,
            skill_id=self.skill_id,
            skill_name=self.skill_name,
            jenkins_job_name=self.jenkins_job_name,
            repository=self.repository,
            ref=self.ref,
            commit_sha=self.commit_sha,
            status=self.status,
            build_number=self.build_number,
            artifact_id=self.artifact_id,
            version=self.version,
            package_type=PackageType(self.package_type),
            content_available=content_available,
            content_conflict=content_conflict,
            created_at=self.created_at,
            updated_at=self.updated_at,
            requested_by=self.requested_by,
        )


@dataclass(frozen=True)
class VersionRecord:
    source: str
    has_bytes: bool


class BuildStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def _session(self) -> Session:
        return Session(self.engine, expire_on_commit=False)

    def list_builds(self) -> list[StoredBuild]:
        with self._session() as session:
            rows = session.scalars(
                select(ProductBuildRow).order_by(ProductBuildRow.created_at, ProductBuildRow.id)
            ).all()
            return [_stored(session, row) for row in rows]

    def get_build(self, build_id: UUID) -> StoredBuild:
        with self._session() as session:
            row = session.get(ProductBuildRow, build_id)
            if row is None:
                raise NotFound("构建不存在")
            return _stored(session, row)

    def create_build(
        self,
        *,
        build_id: UUID,
        name: str,
        jenkins_job_name: str,
        repository: str,
        ref: str,
        commit_sha: str,
        artifact_id: UUID,
        version: str,
        package_type: str,
        queue_url: str | None,
        skill_id: UUID | None = None,
        requested_by: UUID | None = None,
    ) -> StoredBuild:
        now = clock.utcnow()
        row = ProductBuildRow(
            id=build_id,
            name=name,
            jenkins_job_name=jenkins_job_name,
            repository=repository,
            git_ref=ref,
            commit_sha=commit_sha,
            artifact_id=artifact_id,
            version=version,
            package_type=package_type,
            queue_url=queue_url,
            build_number=None,
            status=JobStatus.queued.value,
            content_stored=False,
            skill_id=skill_id,
            requested_by=requested_by,
            created_at=now,
            updated_at=now,
        )
        with self._session() as session:
            session.add(row)
            session.commit()
            return _stored(session, row)

    def create_pending_build(
        self,
        *,
        build_id: UUID,
        artifact_name: str,
        artifact_description: str,
        name: str,
        jenkins_job_name: str,
        repository: str,
        ref: str,
        commit_sha: str,
        version: str,
        package_type: str,
        skill_id: UUID | None,
        requested_by: UUID | None,
    ) -> StoredBuild:
        """Insert the artifact and a queued build in one transaction.

        ``queue_url`` and ``build_number`` stay null until Jenkins accepts the
        trigger. A duplicate artifact name rolls both rows back as 409.
        """

        now = clock.utcnow()
        artifact_id = uuid4()
        artifact = ArtifactRow(
            id=artifact_id,
            name=artifact_name,
            group_name=None,
            description=artifact_description,
            package_type=package_type,
            created_at=now,
        )
        row = ProductBuildRow(
            id=build_id,
            name=name,
            jenkins_job_name=jenkins_job_name,
            repository=repository,
            git_ref=ref,
            commit_sha=commit_sha,
            artifact_id=artifact_id,
            version=version,
            package_type=package_type,
            queue_url=None,
            build_number=None,
            status=JobStatus.queued.value,
            content_stored=False,
            skill_id=skill_id,
            requested_by=requested_by,
            created_at=now,
            updated_at=now,
        )
        with self._session() as session:
            existing = session.scalar(
                select(ArtifactRow.id).where(
                    ArtifactRow.name == artifact_name,
                    ArtifactRow.group_name.is_not_distinct_from(None),
                )
            )
            if existing is not None:
                raise Conflict("同组内制品名称已存在")
            session.add(artifact)
            session.add(row)
            _commit_unique(session, "同组内制品名称已存在")
            return _stored(session, row)

    def attach_queue(self, build_id: UUID, queue_url: str | None) -> StoredBuild:
        with self._session() as session:
            row = session.get(ProductBuildRow, build_id)
            if row is None:
                raise NotFound("构建不存在")
            row.queue_url = queue_url
            row.updated_at = clock.utcnow()
            session.commit()
            return _stored(session, row)

    def mark_not_built(self, build_id: UUID) -> StoredBuild:
        """Terminal failure before a queue item or build number exists.

        Locks the row with ``FOR UPDATE``, the same lock as ``save_run`` and
        ``expire_pending``. Inside the lock, only a row that is still
        ``queued`` with ``build_number`` NULL becomes ``not_built``. Any other
        state is returned unchanged and is not written. ``queue_url`` and
        ``build_number`` are left as they are.
        """

        with self._session() as session:
            row = session.scalar(
                select(ProductBuildRow).where(ProductBuildRow.id == build_id).with_for_update()
            )
            if row is None:
                raise NotFound("构建不存在")
            if row.status != JobStatus.queued.value or row.build_number is not None:
                return _stored(session, row)
            row.status = JobStatus.not_built.value
            row.updated_at = clock.utcnow()
            session.commit()
            return _stored(session, row)

    def expire_pending(self, build_id: UUID, cutoff: datetime) -> bool:
        """Mark an old still-queued build with no number as ``not_built``.

        Uses the same ``FOR UPDATE`` lock as ``save_run``. The queued status,
        null ``build_number``, and ``created_at`` strictly before ``cutoff``
        are checked again inside the lock. ``queue_url`` and ``build_number``
        stay unchanged. Returns whether this call changed the row.
        """

        with self._session() as session:
            row = session.scalar(
                select(ProductBuildRow).where(ProductBuildRow.id == build_id).with_for_update()
            )
            if row is None:
                raise NotFound("构建不存在")
            if (
                row.status != JobStatus.queued.value
                or row.build_number is not None
                or row.created_at >= cutoff
            ):
                return False
            row.status = JobStatus.not_built.value
            row.updated_at = clock.utcnow()
            session.commit()
            return True

    def save_run(
        self,
        build_id: UUID,
        *,
        status: JobStatus,
        build_number: int | None,
    ) -> StoredBuild:
        """Record Jenkins progress. A committed ``not_built`` row is left as it is.

        The status check runs under ``FOR UPDATE`` so a sync that read
        ``queued`` cannot write over ``not_built`` committed while Jenkins
        was being read. ``mark_not_built`` and ``expire_pending`` set that
        status under the same lock, and only while the row is still queued
        with no build number.
        """

        with self._session() as session:
            row = session.scalar(
                select(ProductBuildRow).where(ProductBuildRow.id == build_id).with_for_update()
            )
            if row is None:
                raise NotFound("构建不存在")
            if row.status == JobStatus.not_built.value:
                return _stored(session, row)
            row.status = status.value
            row.build_number = build_number
            row.updated_at = clock.utcnow()
            session.commit()
            return _stored(session, row)

    def mark_content_stored(self, build_id: UUID) -> StoredBuild:
        with self._session() as session:
            row = session.get(ProductBuildRow, build_id)
            if row is None:
                raise NotFound("构建不存在")
            row.content_stored = True
            row.updated_at = clock.utcnow()
            session.commit()
            return _stored(session, row)

    def version_has_bytes(self, artifact_id: UUID, version: str) -> bool:
        record = self.version_record(artifact_id, version)
        return bool(record is not None and record.has_bytes)

    def version_record(self, artifact_id: UUID, version: str) -> VersionRecord | None:
        with self._session() as session:
            row = session.get(ArtifactVersionRow, (artifact_id, version))
            if row is None:
                return None
            return VersionRecord(
                source=row.source,
                has_bytes=bool(row.content_available and row.blob_key),
            )


def _skill_name(session: Session, skill_id: UUID | None) -> str | None:
    if skill_id is None:
        return None
    return session.scalar(select(SkillRow.name).where(SkillRow.id == skill_id))


def _stored(session: Session, row: ProductBuildRow) -> StoredBuild:
    return StoredBuild(
        id=row.id,
        name=row.name,
        jenkins_job_name=row.jenkins_job_name,
        repository=row.repository,
        ref=row.git_ref,
        commit_sha=row.commit_sha,
        artifact_id=row.artifact_id,
        version=row.version,
        package_type=row.package_type,
        queue_url=row.queue_url,
        build_number=row.build_number,
        status=JobStatus(row.status),
        content_stored=row.content_stored,
        skill_id=row.skill_id,
        skill_name=_skill_name(session, row.skill_id),
        requested_by=row.requested_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
