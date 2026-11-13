"""PostgreSQL store for product builds.

Rows keep the commit and the catalog link. Artifact bytes stay in the
platform blob store. Git tokens are not written here.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from kiln import clock
from kiln.db.models import ArtifactVersionRow, ProductBuildRow, SkillRow
from kiln.domain.artifacts import PackageType
from kiln.domain.builds import ProductBuild
from kiln.domain.jobs import JobStatus
from kiln.errors import NotFound


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
        self.created_at = created_at
        self.updated_at = updated_at

    def public(self, *, content_available: bool) -> ProductBuild:
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
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


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
            created_at=now,
            updated_at=now,
        )
        with self._session() as session:
            session.add(row)
            session.commit()
            return _stored(session, row)

    def save_run(
        self,
        build_id: UUID,
        *,
        status: JobStatus,
        build_number: int | None,
    ) -> StoredBuild:
        with self._session() as session:
            row = session.get(ProductBuildRow, build_id)
            if row is None:
                raise NotFound("构建不存在")
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
        with self._session() as session:
            row = session.get(ArtifactVersionRow, (artifact_id, version))
            return bool(row is not None and row.content_available and row.blob_key)


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
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
