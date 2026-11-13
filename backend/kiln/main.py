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
from kiln.api import (
    accounts,
    artifacts,
    auth,
    builds,
    dependency_proxy,
    git,
    health,
    jobs,
    llm,
    retrieve,
    skills,
)
from kiln.config import Settings, get_settings
from kiln.db.builds import BuildStore
from kiln.db.engine import create_db_engine
from kiln.db.jobs import JobStore
from kiln.db.llm_catalog import LlmCatalogStore
from kiln.db.migrate import upgrade_database
from kiln.db.seed import seed_rbac
from kiln.db.store import PostgresStore
from kiln.domain.llm_catalog import warn_env_seed
from kiln.errors import AppError
from kiln.executor.jenkins import JenkinsClient
from kiln.git.source import build_git_source
from kiln.llm.provider import build_provider
from kiln.nexus.client import NexusClient
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
    "已登录用户的写入会按现有权限目录校验，包括 account.manage、llm.manage、"
    "artifact.upload、artifact.download、skill.use、skill.submit、skill.publish、"
    "executor.run 和 agent.qa。"
    "POST /skills/{id}/reject 需要 skill.publish，把审核中的 skill 退回草稿，须填写原因。"
    "POST /jobs 需要 executor.run。POST /builds 需要 executor.run 与 skill.use，"
    "并且必须带已发布 skill 的 skill_id。POST /llm/chat 需要 agent.qa。"
    "只读角色不能调用这三处写入。"
    "GET /accounts 与 GET /accounts/{id} 需要 account.read，"
    "GET /artifacts 与 GET /artifacts/{id} 需要 artifact.read，"
    "GET /skills 与 GET /skills/{id} 需要 skill.read。"
    "GET /executor、GET /dependency-proxy、GET /jobs、GET /jobs/{id}、GET /jobs/{id}/log、"
    "GET /builds、GET /builds/{id} 和 GET /builds/{id}/log 需要 executor.read。"
    "GET /git/source、GET /git/repositories 和 GET /git/commits 需要 git.read。"
    "只读角色可以查看账号、skill 与制品，没有 executor.read 和 git.read。"
    "GET /llm/provider、GET /rbac/roles、GET /rbac/permissions 和取包元数据"
    "仍只需会话或机器令牌。GET /llm/catalog 与 PUT /llm/catalog 需要 llm.manage，"
    "缺少该权限时 403 的 detail 写明 llm.manage。"
    "GET /retrieve/{id}/content 需要 artifact.download。"
    "权限不足返回 403，正文是「没有权限」，并且先于未配置检查。"
    "/health 与 /docs 不接受写入。"
    "不运行 skill 步骤，也不把制品部署到运行环境。"
    "配置 JENKINS_URL 后，兼容执行器可以在 Jenkins 上创建任务、跟踪状态并读取日志。"
    "代码源是可替换适配器：GIT_PROVIDER=gitea 是实验室实现，GIT_PROVIDER=gitlab 是生产代码源。"
    "这两处地址只来自环境变量。仓库不写生产 GitLab 地址。"
    "可选依赖代理对接默认关闭；制品不发布到 Nexus。"
    "未设置 KILN_NEXUS_URL 时，产品构建与现在相同，GET /dependency-proxy 不向外发起调用。"
    "模型调用走 OpenAI 兼容协议。提供方、端点、密钥和超时由环境变量配置；"
    "未就绪时可使用 mock 或 off。"
    "模型目录和角色分配可在管理页保存，首次从 LLM_MODEL / LLM_MODELS 种子。"
    "GET /llm/provider 仍只需登录。没有 agent.qa 的调用者，models 为空，default_model 为空。"
    "其余调用者在尚未托管时看到环境变量目录；"
    "已托管且提供方为 openai 时看到自己可用的模型 id。"
    "mock 与 off 的 models 为空。对话只更换 model id，不切换提供方。"
    "对话可在授权下通过工具起草 skill 草稿，或在 skill 发布后发起构建。"
    "这些工具只在 openai 提供方下运行；mock 与 off 不调用工具。"
    "连接参数只从环境变量读取。已保存的模型目录保存在 PostgreSQL，不含密钥。"
)


def create_app(
    settings: Settings | None = None,
    http_client: httpx.Client | None = None,
    nexus_http_client: httpx.Client | None = None,
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
    owns_nexus_http = nexus_http_client is None
    if nexus_http_client is None:
        nexus_http = httpx.Client(timeout=resolved.kiln_nexus_timeout_seconds, trust_env=False)
    else:
        nexus_http = nexus_http_client
    nexus = (
        NexusClient(
            resolved.kiln_nexus_url,
            resolved.kiln_nexus_repository,
            resolved.kiln_nexus_username,
            resolved.nexus_password_value(),
            nexus_http,
        )
        if resolved.nexus_configured
        else None
    )
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
            if owns_nexus_http:
                nexus_http.close()
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
    app.state.nexus = nexus
    app.state.llm = provider
    app.state.llm_catalog = LlmCatalogStore(engine)
    warn_env_seed(
        resolved,
        source_is_env=app.state.llm_catalog.follows_environment(),
    )
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
        headers: dict[str, str] = {}
        if exc.status_code == 401:
            headers["WWW-Authenticate"] = "Bearer"
        if _request.url.path == f"{API_PREFIX}/auth/me":
            headers["Cache-Control"] = "no-store"
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=headers or None,
        )

    app.include_router(health.router)
    app.include_router(auth.router, prefix=API_PREFIX)
    app.include_router(accounts.router, prefix=API_PREFIX)
    app.include_router(artifacts.router, prefix=API_PREFIX)
    app.include_router(retrieve.router, prefix=API_PREFIX)
    app.include_router(skills.router, prefix=API_PREFIX)
    app.include_router(llm.router, prefix=API_PREFIX)
    app.include_router(jobs.router, prefix=API_PREFIX)
    app.include_router(dependency_proxy.router, prefix=API_PREFIX)
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
