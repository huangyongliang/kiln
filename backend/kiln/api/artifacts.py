"""Artifact catalog outline. Versions are registered with checksums, not file bytes."""

from uuid import UUID

from fastapi import APIRouter, Depends

from kiln.api.deps import get_store
from kiln.domain.artifacts import (
    Artifact,
    ArtifactCreate,
    ArtifactList,
    ArtifactVersion,
    ArtifactVersionCreate,
    DownloadReservationCreate,
    Reservation,
)
from kiln.memory import MemoryStore

router = APIRouter(prefix="/artifacts", tags=["制品"])


@router.get("", response_model=ArtifactList, summary="列出制品")
def list_artifacts(store: MemoryStore = Depends(get_store)) -> ArtifactList:
    return ArtifactList(items=store.list_artifacts())


@router.post("", response_model=Artifact, status_code=201, summary="登记制品")
def create_artifact(payload: ArtifactCreate, store: MemoryStore = Depends(get_store)) -> Artifact:
    return store.create_artifact(payload)


@router.get("/{artifact_id}", response_model=Artifact, summary="查看制品")
def get_artifact(artifact_id: UUID, store: MemoryStore = Depends(get_store)) -> Artifact:
    return store.get_artifact(artifact_id)


@router.post(
    "/{artifact_id}/versions",
    response_model=ArtifactVersion,
    status_code=201,
    summary="登记不可变版本",
)
def add_version(
    artifact_id: UUID,
    payload: ArtifactVersionCreate,
    store: MemoryStore = Depends(get_store),
) -> ArtifactVersion:
    return store.add_version(artifact_id, payload)


@router.post(
    "/{artifact_id}/versions/{version}/download-reservations",
    response_model=Reservation,
    status_code=201,
    summary="创建下载预约",
)
def reserve_download(
    artifact_id: UUID,
    version: str,
    payload: DownloadReservationCreate,
    store: MemoryStore = Depends(get_store),
) -> Reservation:
    return store.reserve_download(artifact_id, version, payload)
