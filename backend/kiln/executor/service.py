"""Create a Jenkins job and keep its kiln record in step with the controller."""

from uuid import UUID, uuid4

from kiln.db.jobs import JobStore, StoredJob
from kiln.domain.jobs import TERMINAL_STATUSES, JobCreate, JobLog, JobStatus
from kiln.errors import Conflict
from kiln.executor.jenkins import JenkinsClient


def create_job(
    store: JobStore,
    jenkins: JenkinsClient,
    payload: JobCreate,
    *,
    requested_by: UUID | None = None,
) -> StoredJob:
    job_id = uuid4()
    jenkins_job_name = f"kiln-{job_id.hex}"
    display_name = payload.name or jenkins_job_name
    jenkins.create_job(jenkins_job_name, payload.marker)
    try:
        queue_url = jenkins.trigger_build(jenkins_job_name)
        return store.create_job(
            job_id=job_id,
            name=display_name,
            jenkins_job_name=jenkins_job_name,
            marker=payload.marker,
            queue_url=queue_url,
            requested_by=requested_by,
        )
    except Exception:
        try:
            jenkins.delete_job(jenkins_job_name)
        except Exception:
            pass
        raise


def tracked_job(store: JobStore, jenkins: JenkinsClient, job_id: UUID) -> StoredJob:
    current = store.get_job(job_id)
    if current.status in TERMINAL_STATUSES and current.build_number is not None:
        return current
    status_name, number = jenkins.read_progress(
        current.jenkins_job_name,
        current.queue_url,
        current.build_number,
    )
    status = JobStatus(status_name)
    if status == current.status and number == current.build_number:
        return current
    return store.save_run(current.id, status=status, build_number=number)


def list_tracked(store: JobStore, jenkins: JenkinsClient) -> list[StoredJob]:
    items: list[StoredJob] = []
    for job in store.list_jobs():
        if job.status in TERMINAL_STATUSES and job.build_number is not None:
            items.append(job)
            continue
        items.append(tracked_job(store, jenkins, job.id))
    return items


def job_log(store: JobStore, jenkins: JenkinsClient, job_id: UUID) -> JobLog:
    current = tracked_job(store, jenkins, job_id)
    if current.build_number is None:
        raise Conflict("这次构建还没有日志")
    text, truncated = jenkins.read_log(current.jenkins_job_name, current.build_number)
    return JobLog(
        job_id=current.id,
        build_number=current.build_number,
        text=text,
        truncated=truncated,
    )
