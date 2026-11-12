"""Liveness endpoint. It does not check Postgres or the model endpoint."""

from fastapi import APIRouter
from pydantic import BaseModel

from kiln import __version__

router = APIRouter(tags=["健康检查"])


class Health(BaseModel):
    status: str
    service: str
    version: str


class Root(BaseModel):
    service: str
    health: str
    docs: str


@router.get("/", response_model=Root, include_in_schema=False)
def root() -> Root:
    return Root(service="kiln", health="/health", docs="/docs")


@router.get("/health", response_model=Health, summary="健康检查")
def health() -> Health:
    return Health(status="ok", service="kiln", version=__version__)
