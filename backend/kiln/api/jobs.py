"""Jenkins compat executor HTTP API.

Creating a job, reading its status, and reading its log require a user session
or the machine token. These routes do not deploy to a runtime.
"""

from uuid import UUID

from fastapi import APIRouter, Depends

from kiln.api.deps import get_jenkins, get_job_store, get_settings
from kiln.auth import require_auth
from kiln.config import Settings
from kiln.db.jobs import JobStore
from kiln.domain.jobs import ExecutorInfo, Job, JobCreate, JobList, JobLog
from kiln.executor.jenkins import JenkinsClient
from kiln.executor.service import create_job, job_log, list_tracked, tracked_job

router = APIRouter(tags=["执行器"], dependencies=[Depends(require_auth)])


@router.get("/executor", response_model=ExecutorInfo, summary="执行器状态")
def executor_info(settings: Settings = Depends(get_settings)) -> ExecutorInfo:
    configured = settings.executor_configured
    return ExecutorInfo(
        kind="jenkins" if configured else "off",
        configured=configured,
        base_url=settings.jenkins_url or None,
        deploys_to_runtime=False,
    )


@router.get("/jobs", response_model=JobList, summary="列出 Jenkins 任务")
def list_jobs(
    store: JobStore = Depends(get_job_store),
    jenkins: JenkinsClient = Depends(get_jenkins),
) -> JobList:
    return JobList(items=[item.public() for item in list_tracked(store, jenkins)])


@router.post("/jobs", response_model=Job, status_code=201, summary="在 Jenkins 上创建任务")
def post_job(
    payload: JobCreate,
    store: JobStore = Depends(get_job_store),
    jenkins: JenkinsClient = Depends(get_jenkins),
) -> Job:
    return create_job(store, jenkins, payload).public()


@router.get("/jobs/{job_id}", response_model=Job, summary="查看任务状态")
def get_job(
    job_id: UUID,
    store: JobStore = Depends(get_job_store),
    jenkins: JenkinsClient = Depends(get_jenkins),
) -> Job:
    return tracked_job(store, jenkins, job_id).public()


@router.get("/jobs/{job_id}/log", response_model=JobLog, summary="读取 Jenkins 日志")
def get_job_log(
    job_id: UUID,
    store: JobStore = Depends(get_job_store),
    jenkins: JenkinsClient = Depends(get_jenkins),
) -> JobLog:
    return job_log(store, jenkins, job_id)
