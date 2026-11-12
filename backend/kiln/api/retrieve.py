"""Stable retrieve API outline. The path is reserved for later byte downloads."""

from uuid import UUID

from fastapi import APIRouter, Depends

from kiln.api.deps import get_store
from kiln.domain.artifacts import RetrieveResult
from kiln.memory import MemoryStore

router = APIRouter(tags=["取包"])


@router.get(
    "/retrieve/{reservation_id}",
    response_model=RetrieveResult,
    summary="按预约取包",
)
def retrieve(reservation_id: UUID, store: MemoryStore = Depends(get_store)) -> RetrieveResult:
    return store.get_retrieve(reservation_id)
