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
from kiln.errors import AppError
from kiln.llm.provider import build_provider
from kiln.memory import MemoryStore
from kiln.paths import API_PREFIX

DESCRIPTION = (
    "kiln 一期骨架。账号、制品、skill 与对话接口是领域大纲："
    "记录保存在进程内存中，不执行鉴权，不接收制品字节，不运行 skill 步骤。"
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
    store = MemoryStore()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
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
    app.state.store = store
    app.state.llm = provider

    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins(),
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Accept", "Content-Type"],
    )

    @app.exception_handler(AppError)
    async def handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

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
