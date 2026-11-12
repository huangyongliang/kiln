"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from kiln import __version__
from kiln.api import accounts, artifacts, auth, builds, git, health, jobs, llm, retrieve, skills
from kiln.config import Settings, get_settings
from kiln.db.builds import BuildStore
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
    "和产品构建保存在 PostgreSQL 中。制品字节保存在平台自管的本地目录，进程重启后仍可取回。"
    "版本保存字节后 content_available 为 true。"
    "产品构建从已配置的代码源检出提交，在 Jenkins 上运行该提交里的 kiln-build.sh，"
    "再把产出的 kiln-dist/artifact.bin 写入平台目录。这不会把制品部署到运行环境。"
    "API 启动时自动迁移。"
    "本地登录把口令存成哈希，会话保存在 PostgreSQL。控制台使用 httpOnly cookie，"
    "API 客户端使用登录返回的 Bearer 令牌。"
    "KILN_API_TOKEN 仍是必填的机器和开发访问令牌，拥有目录中的全部权限。"
    "未配置公司用户系统时，本地口令登录可用。配置之后，用户信息同步只接收身份和在职状态，"
    "不把公司侧的组或角色写入 kiln 权限。kiln 的授权仍留在平台上。"
    "已登录用户的写入会按现有权限目录校验，包括 account.manage、artifact.upload、"
    "artifact.download、skill.use 和 skill.publish。"
    "/health 与 /docs 不接受写入。"
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
    build_store = BuildStore(engine)
    jenkins_http = httpx.Client(timeout=resolved.jenkins_timeout_seconds, trust_env=False)
    git_http = httpx.Client(timeout=resolved.git_timeout_seconds, trust_env=False)
    oidc_http = httpx.Client(timeout=30.0, trust_env=False)
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
            oidc_http.close()
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
    app.state.builds = build_store
    app.state.jenkins = jenkins
    app.state.git_source = git_source
    app.state.llm = provider
    app.state.oidc_http = oidc_http

    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Accept", "Authorization", "Content-Type"],
    )

    @app.exception_handler(RequestValidationError)
    async def hide_password_values(
        _request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": _redact_password(exc.errors())})

    @app.exception_handler(AppError)
    async def handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=headers,
        )

    app.include_router(health.router)
    app.include_router(auth.router, prefix=API_PREFIX)
    app.include_router(accounts.router, prefix=API_PREFIX)
    app.include_router(artifacts.router, prefix=API_PREFIX)
    app.include_router(retrieve.router, prefix=API_PREFIX)
    app.include_router(skills.router, prefix=API_PREFIX)
    app.include_router(llm.router, prefix=API_PREFIX)
    app.include_router(jobs.router, prefix=API_PREFIX)
    app.include_router(builds.router, prefix=API_PREFIX)
    app.include_router(git.router, prefix=API_PREFIX)
    return app


def _redact_password(detail: object) -> object:
    encoded = jsonable_encoder(detail)
    return _redact_encoded(encoded)


def _redact_encoded(detail: object) -> object:
    if isinstance(detail, list):
        return [_redact_encoded(item) for item in detail]
    if isinstance(detail, dict):
        cleaned = {key: _redact_encoded(value) for key, value in detail.items()}
        loc = cleaned.get("loc")
        if isinstance(loc, list) and "password" in loc:
            cleaned.pop("input", None)
        hidden = cleaned.get("input")
        if isinstance(hidden, dict) and "password" in hidden:
            cleaned["input"] = {key: value for key, value in hidden.items() if key != "password"}
        return cleaned
    return detail


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "kiln.main:create_app",
        factory=True,
        host=settings.kiln_http_host,
        port=settings.kiln_http_port,
    )
