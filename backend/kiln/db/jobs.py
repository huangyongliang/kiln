"""PostgreSQL store for Jenkins compat jobs."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from kiln import clock
from kiln.db.models import ExecutorJobRow
from kiln.domain.jobs import Job, JobStatus
from kiln.errors import NotFound


class StoredJob:
    def __init__(
        self,
        *,
        id: UUID,
        name: str,
        jenkins_job_name: str,
        marker: str,
        queue_url: str | None,
        build_number: int | None,
        status: JobStatus,
        requested_by: UUID | None,
        created_at: datetime,
        updated_at: datetime,
    ) -> None:
        self.id = id
        self.name = name
        self.jenkins_job_name = jenkins_job_name
        self.marker = marker
        self.queue_url = queue_url
        self.build_number = build_number
        self.status = status
        self.requested_by = requested_by
        self.created_at = created_at
        self.updated_at = updated_at

    def public(self) -> Job:
        return Job(
            id=self.id,
            name=self.name,
            jenkins_job_name=self.jenkins_job_name,
            marker=self.marker,
            status=self.status,
            build_number=self.build_number,
            created_at=self.created_at,
            updated_at=self.updated_at,
            requested_by=self.requested_by,
        )


class JobStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def _session(self) -> Session:
        return Session(self.engine, expire_on_commit=False)

    def list_jobs(self) -> list[StoredJob]:
        with self._session() as session:
            rows = session.scalars(
                select(ExecutorJobRow).order_by(ExecutorJobRow.created_at, ExecutorJobRow.id)
            ).all()
            return [_stored(row) for row in rows]

    def get_job(self, job_id: UUID) -> StoredJob:
        with self._session() as session:
            row = session.get(ExecutorJobRow, job_id)
            if row is None:
                raise NotFound("任务不存在")
            return _stored(row)

    def create_job(
        self,
        *,
        job_id: UUID,
        name: str,
        jenkins_job_name: str,
        marker: str,
        queue_url: str | None,
        requested_by: UUID | None = None,
    ) -> StoredJob:
        now = clock.utcnow()
        row = ExecutorJobRow(
            id=job_id,
            name=name,
            jenkins_job_name=jenkins_job_name,
            marker=marker,
            queue_url=queue_url,
            build_number=None,
            status=JobStatus.queued.value,
            requested_by=requested_by,
            created_at=now,
            updated_at=now,
        )
        with self._session() as session:
            session.add(row)
            session.commit()
            return _stored(row)

    def save_run(
        self,
        job_id: UUID,
        *,
        status: JobStatus,
        build_number: int | None,
    ) -> StoredJob:
        with self._session() as session:
            row = session.get(ExecutorJobRow, job_id)
            if row is None:
                raise NotFound("任务不存在")
            row.status = status.value
            row.build_number = build_number
            row.updated_at = clock.utcnow()
            session.commit()
            return _stored(row)


def _stored(row: ExecutorJobRow) -> StoredJob:
    return StoredJob(
        id=row.id,
        name=row.name,
        jenkins_job_name=row.jenkins_job_name,
        marker=row.marker,
        queue_url=row.queue_url,
        build_number=row.build_number,
        status=JobStatus(row.status),
        requested_by=row.requested_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
