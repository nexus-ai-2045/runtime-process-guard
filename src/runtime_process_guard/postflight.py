"""Purely bounded postflight verification for one owned process group."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Literal


PostflightStatus = Literal["gone", "still-running", "unknown"]


def verify_owned_disappearance(
    active_count: Callable[[], int],
    *,
    timeout_seconds: float,
    poll_seconds: float = 0.05,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> PostflightStatus:
    """Verify an already-owned group without stopping or adopting any process."""

    if timeout_seconds < 0 or poll_seconds <= 0:
        raise ValueError("postflight timeouts must be non-negative")
    deadline = clock() + timeout_seconds
    while True:
        try:
            count = active_count()
        except (OSError, RuntimeError):
            return "unknown"
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            return "unknown"
        if count == 0:
            return "gone"
        if clock() >= deadline:
            return "still-running"
        sleep(poll_seconds)
