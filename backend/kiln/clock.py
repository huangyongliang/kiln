"""Clock used by the domain store so tests can move time."""

from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
