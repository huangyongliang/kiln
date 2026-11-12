"""Clock used by in-memory stubs so tests can move time."""

from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
