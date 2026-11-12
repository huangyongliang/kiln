"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from kiln import __version__
from kiln.api import accounts, artifacts, git, health, jobs, llm, retrieve, skills
from kiln.config import Settings, get_settings
from kiln.db.engine import create_db_engine
from kiln.db.jobs import JobStore
from kiln.db.migrate import upgrade_database
from kiln.db.seed import seed_rbac
from kiln.db.store import PostgresStore
from kiln.errors import AppError
from kiln.executor.jenkins import JenkinsClient
from kiln.git.source import build_git_source
from kiln.llm.provider import build_provider
from kiln.paths import API_PREFIX
from kiln.storage.local import LocalBlobStore

DESCRIPTION = (
    "kiln 一期骨架。账号、RBAC 目录、制品、版本、下载预约、skill 与 Jenkins 任务"
    "保存在 PostgreSQL 中。制品字节保存在平台自管的本地目录，进程重启后仍可取回。"
    "版本保存字节后 content_available 为 true。"
    "API 启动时自动迁移。"
    "/api/v1 需要请求头 Authorization: Bearer <KILN_API_TOKEN>。"
    "这不是登录，也不按角色鉴权。/health 与 /docs 不接受写入。"
    "不运行 skill 步骤，也不把制品部署到运行环境。"
    "配置 JENKINS_URL 后，兼容执行器可以在 Jenkins 上创建任务、跟踪状态并读取日志。"
    "代码源是可替换适配器：GIT_PROVIDER=gitea 是实验室实现，GIT_PROVIDER=gitlab 是生产代码源。"
    "这两处地址只来自环境变量。仓库不写生产 GitLab 地址。Nexus 集成还没有接上，也没有 Nexus 地址。"
    "模型调用走 OpenAI 兼容协议，端点由环境变量配置；未就绪时可使用 mock 或 off。"
    "配置只从环境变量读取。"
)


def create_app(
    settings: Settings | None = None,
    http_client: httpx.Client | None = None,
) -> FastAPI:
    resolved = settings if settings is not None else get_settings()
    owns_client = http_client is None
    if http_client is None:
        client = httpx.Client(timeout=resolved.llm_timeout_seconds)
    else:
        client = http_client
    provider = build_provider(resolved, client)
    engine = create_db_engine(resolved.database_url)
    blobs = LocalBlobStore(Path(resolved.kiln_artifact_root))
    store = PostgresStore(engine, blobs)
    jobs_store = JobStore(engine)
    jenkins_http = httpx.Client(timeout=resolved.jenkins_timeout_seconds, trust_env=False)
    git_http = httpx.Client(timeout=resolved.git_timeout_seconds, trust_env=False)
    jenkins = (
        JenkinsClient(
            resolved.jenkins_url,
            resolved.jenkins_user,
            resolved.jenkins_api_token_value(),
            jenkins_http,
        )
        if resolved.executor_configured
        else None
    )
    git_source = build_git_source(resolved, git_http)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            blobs.prepare()
            upgrade_database(resolved.database_url)
            seed_rbac(engine)
            yield
        finally:
            engine.dispose()
            jenkins_http.close()
            git_http.close()
            if owns_client:
                client.close()

    app = FastAPI(
        title="kiln",
        version=__version__,
        summary="制品构建平台一期骨架",
        description=DESCRIPTION,
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.state.http_client = client
    app.state.engine = engine
    app.state.store = store
    app.state.blobs = blobs
    app.state.jobs = jobs_store
    app.state.jenkins = jenkins
    app.state.git_source = git_source
    app.state.llm = provider

    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins(),
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Accept", "Authorization", "Content-Type"],
    )

    @app.exception_handler(AppError)
    async def handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=headers,
        )

    app.include_router(health.router)
    app.include_router(accounts.router, prefix=API_PREFIX)
    app.include_router(artifacts.router, prefix=API_PREFIX)
    app.include_router(retrieve.router, prefix=API_PREFIX)
    app.include_router(skills.router, prefix=API_PREFIX)
    app.include_router(llm.router, prefix=API_PREFIX)
    app.include_router(jobs.router, prefix=API_PREFIX)
    app.include_router(git.router, prefix=API_PREFIX)
    return app


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "kiln.main:create_app",
        factory=True,
        host=settings.kiln_http_host,
        port=settings.kiln_http_port,
    )
