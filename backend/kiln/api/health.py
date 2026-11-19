"""Liveness and readiness.

GET /health does not check Postgres or the model endpoint.
GET /ready runs SELECT 1 on the existing engine. The statement timeout is
fixed at 2 seconds. A connection, query, or timeout failure is HTTP 503 with
a fixed detail. The response and logs omit the DSN, password, and exception
text. Readiness does not check the model, Jenkins, Nexus, the source adapter,
or the alembic head.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Engine

from kiln import __version__

logger = logging.getLogger(__name__)

router = APIRouter(tags=["健康检查"])

_DB_UNAVAILABLE = "数据库不可用"


class Health(BaseModel):
    status: str
    service: str
    version: str


class Root(BaseModel):
    service: str
    health: str
    ready: str
    docs: str


@router.get("/", response_model=Root, include_in_schema=False)
def root() -> Root:
    return Root(service="kiln", health="/health", ready="/ready", docs="/docs")


@router.get("/health", response_model=Health, summary="健康检查")
def health() -> Health:
    return Health(status="ok", service="kiln", version=__version__)


@router.get("/ready", response_model=Health, summary="就绪检查")
def ready(request: Request) -> Health:
    try:
        _probe_database(request.app.state.engine)
    except Exception:
        # Fixed message only. Do not log the exception, DSN, or password.
        logger.warning("就绪检查未能查询数据库")
        raise HTTPException(status_code=503, detail=_DB_UNAVAILABLE) from None
    return Health(status="ok", service="kiln", version=__version__)


def _probe_database(engine: Engine) -> None:
    with engine.connect() as connection:
        connection.execute(text("SET LOCAL statement_timeout = '2s'"))
        connection.execute(text("SELECT 1"))
