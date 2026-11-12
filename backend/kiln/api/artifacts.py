"""Artifact catalog and platform-owned byte storage.

Listing and reading an artifact require `artifact.read`.
"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from kiln.api.deps import get_store
from kiln.auth import require_auth, require_permission
from kiln.config import Settings
from kiln.db.store import PostgresStore
from kiln.domain.artifacts import (
    Artifact,
    ArtifactCreate,
    ArtifactList,
    ArtifactSource,
    ArtifactVersion,
    ArtifactVersionCreate,
    Checksum,
    DownloadReservationCreate,
    Reservation,
)
from kiln.errors import BadRequest, PayloadTooLarge

router = APIRouter(
    prefix="/artifacts",
    tags=["制品"],
    dependencies=[Depends(require_auth)],
)


@router.get(
    "",
    response_model=ArtifactList,
    summary="列出制品",
    dependencies=[Depends(require_permission("artifact.read"))],
)
def list_artifacts(store: PostgresStore = Depends(get_store)) -> ArtifactList:
    return ArtifactList(items=store.list_artifacts())


@router.post(
    "",
    response_model=Artifact,
    status_code=201,
    summary="登记制品",
    dependencies=[Depends(require_permission("artifact.upload"))],
)
def create_artifact(payload: ArtifactCreate, store: PostgresStore = Depends(get_store)) -> Artifact:
    return store.create_artifact(payload)


@router.get(
    "/{artifact_id}",
    response_model=Artifact,
    summary="查看制品",
    dependencies=[Depends(require_permission("artifact.read"))],
)
def get_artifact(artifact_id: UUID, store: PostgresStore = Depends(get_store)) -> Artifact:
    return store.get_artifact(artifact_id)


@router.post(
    "/{artifact_id}/versions",
    response_model=ArtifactVersion,
    status_code=201,
    summary="登记不可变版本",
    dependencies=[Depends(require_permission("artifact.upload"))],
)
def add_version(
    artifact_id: UUID,
    payload: ArtifactVersionCreate,
    store: PostgresStore = Depends(get_store),
) -> ArtifactVersion:
    return store.add_version(artifact_id, payload)


async def _read_limited_body(request: Request) -> bytes:
    settings: Settings = request.app.state.settings
    limit = settings.kiln_artifact_max_bytes
    declared = request.headers.get("content-length")
    if declared is not None and declared.strip():
        try:
            announced = int(declared)
        except ValueError as exc:
            raise BadRequest("Content-Length 不正确") from exc
        if announced < 0:
            raise BadRequest("Content-Length 不正确")
        if announced > limit:
            raise PayloadTooLarge(f"制品字节超过上限（{limit} 字节）")
    size = 0
    chunks: list[bytes] = []
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise PayloadTooLarge(f"制品字节超过上限（{limit} 字节）")
        chunks.append(chunk)
    return b"".join(chunks)


@router.put(
    "/{artifact_id}/versions/{version}/content",
    response_model=ArtifactVersion,
    responses={
        200: {"model": ArtifactVersion, "description": "相同字节已经保存"},
        201: {"model": ArtifactVersion, "description": "制品字节已保存"},
    },
    summary="保存制品字节",
    dependencies=[Depends(require_permission("artifact.upload"))],
)
def store_version_content(
    artifact_id: UUID,
    version: str,
    body: bytes = Depends(_read_limited_body),
    checksum_algorithm: Literal["sha256", "sha512"] = Header(alias="X-Kiln-Checksum-Algorithm"),
    checksum_value: str = Header(alias="X-Kiln-Checksum"),
    source: ArtifactSource = Header(default=ArtifactSource.upload, alias="X-Kiln-Source"),
    store: PostgresStore = Depends(get_store),
) -> JSONResponse:
    try:
        checksum = Checksum(algorithm=checksum_algorithm, value=checksum_value)
    except ValidationError as exc:
        raise BadRequest("校验和格式不正确") from exc
    stored, created = store.store_version_bytes(
        artifact_id,
        version,
        body,
        checksum,
        source,
    )
    return JSONResponse(
        status_code=201 if created else 200,
        content=jsonable_encoder(stored),
    )


@router.post(
    "/{artifact_id}/versions/{version}/download-reservations",
    response_model=Reservation,
    status_code=201,
    summary="创建下载预约",
    dependencies=[Depends(require_permission("artifact.download"))],
)
def reserve_download(
    artifact_id: UUID,
    version: str,
    payload: DownloadReservationCreate,
    store: PostgresStore = Depends(get_store),
) -> Reservation:
    return store.reserve_download(artifact_id, version, payload)
