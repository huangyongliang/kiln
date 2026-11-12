"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from kiln import __version__
from kiln.api import accounts, artifacts, health, llm, retrieve, skills
from kiln.config import Settings, get_settings
from kiln.db.engine import create_db_engine
from kiln.db.migrate import upgrade_database
from kiln.db.seed import seed_rbac
from kiln.db.store import PostgresStore
from kiln.errors import AppError
from kiln.llm.provider import build_provider
from kiln.paths import API_PREFIX

DESCRIPTION = (
    "kiln 一期骨架。账号、RBAC 目录、制品、版本、下载预约与 skill 保存在 PostgreSQL 中。"
    "API 启动时自动迁移，进程重启后记录仍在。"
    "/api/v1 需要请求头 Authorization: Bearer <KILN_API_TOKEN>。"
    "这不是登录，也不按角色鉴权。/health 与 /docs 不接受写入。"
    "不接收制品字节，不运行 skill 步骤。"
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
    store = PostgresStore(engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            upgrade_database(resolved.database_url)
            seed_rbac(engine)
            yield
        finally:
            engine.dispose()
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
    return app


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "kiln.main:create_app",
        factory=True,
        host=settings.kiln_http_host,
        port=settings.kiln_http_port,
    )
