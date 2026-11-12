"""Stable HTTP paths shared by routers and in-memory records."""

from uuid import UUID

API_PREFIX = "/api/v1"


def retrieve_path(reservation_id: UUID) -> str:
    return f"{API_PREFIX}/retrieve/{reservation_id}"
