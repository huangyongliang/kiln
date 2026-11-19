"""In-process sliding window for local password login failures.

Only ``POST /api/v1/auth/login`` records failures. A failure counts while
its timestamp is strictly younger than ``window_seconds``; older ones are
dropped. Success clears that username key and leaves the IP key in place.

The login route is a synchronous ``def``. FastAPI runs it on a worker
thread, so the counter lock is a ``threading.Lock``. An ``asyncio.Lock``
would not serialize those calls.
"""

import math
import threading
import time
from collections.abc import Callable


def normalize_username(username: str) -> str | None:
    """Trim and case-fold a login username. An empty result is not a key."""

    key = username.strip().casefold()
    if not key:
        return None
    return key


def peer_host(client: object | None) -> str | None:
    """TCP peer from ``request.client``, or None when that peer is missing.

    Callers must not substitute ``X-Forwarded-For`` or ``X-Real-IP``.
    """

    if client is None:
        return None
    host = getattr(client, "host", None)
    if not isinstance(host, str) or host == "":
        return None
    return host


class LoginLimiter:
    """Sliding-window failure counts owned by one API process."""

    def __init__(
        self,
        *,
        window_seconds: int,
        username_max_failures: int,
        ip_max_failures: int,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.window_seconds = window_seconds
        self.username_max_failures = username_max_failures
        self.ip_max_failures = ip_max_failures
        self.clock = clock or time.monotonic
        self.lock = threading.Lock()
        self._username_failures: dict[str, list[float]] = {}
        self._ip_failures: dict[str, list[float]] = {}

    def blocked(self, username: str | None, ip: str | None) -> int | None:
        """Retry-After seconds when a key is already at its cap.

        Does not record another failure. ``None`` means the caller may
        authenticate.
        """

        with self.lock:
            return self._apply(username, ip, record=False)

    def record_failure(self, username: str | None, ip: str | None) -> int | None:
        """Count a password failure. Retry-After when that count reaches a cap."""

        with self.lock:
            return self._apply(username, ip, record=True)

    def clear_username(self, username: str) -> None:
        """Drop one username key after a session is issued. The IP key stays."""

        with self.lock:
            self._username_failures.pop(username, None)

    def _apply(self, username: str | None, ip: str | None, *, record: bool) -> int | None:
        now = self.clock()
        username_stamps = self._update(self._username_failures, username, now, record=record)
        ip_stamps = self._update(self._ip_failures, ip, now, record=record)
        waits: list[int] = []
        if username_stamps is not None and len(username_stamps) >= self.username_max_failures:
            waits.append(self._seconds_until(username_stamps[0], now))
        if ip_stamps is not None and len(ip_stamps) >= self.ip_max_failures:
            waits.append(self._seconds_until(ip_stamps[0], now))
        if not waits:
            return None
        return max(waits)

    def _update(
        self,
        table: dict[str, list[float]],
        key: str | None,
        now: float,
        *,
        record: bool,
    ) -> list[float] | None:
        if key is None:
            return None
        kept = [stamp for stamp in table.get(key, ()) if now - stamp < self.window_seconds]
        if record:
            kept.append(now)
        if kept:
            table[key] = kept
        else:
            table.pop(key, None)
        return kept

    def _seconds_until(self, oldest: float, now: float) -> int:
        """Whole seconds until ``oldest`` leaves the window, at least 1."""

        remaining = oldest + self.window_seconds - now
        seconds = math.ceil(remaining)
        if seconds < 1:
            return 1
        return seconds
