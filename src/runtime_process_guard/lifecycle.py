"""Pure lifecycle policy for processes launched and owned by this guard."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal


LifecycleAction = Literal[
    "keep", "graceful-shutdown", "terminate-managed-job", "report-only"
]


def _utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class LifecyclePolicy:
    idle_timeout: timedelta
    grace_timeout: timedelta

    def __post_init__(self) -> None:
        if self.idle_timeout <= timedelta(0):
            raise ValueError("idle_timeout must be positive")
        if self.grace_timeout <= timedelta(0):
            raise ValueError("grace_timeout must be positive")


@dataclass(frozen=True)
class LifecycleObservation:
    managed: bool
    owner_matches: bool
    owner_alive: bool
    active_requests: int
    last_activity_at: datetime
    shutdown_requested_at: datetime | None

    def __post_init__(self) -> None:
        for field in ("managed", "owner_matches", "owner_alive"):
            if not isinstance(getattr(self, field), bool):
                raise ValueError(f"{field} must be a boolean")
        if not isinstance(self.active_requests, int) or self.active_requests < 0:
            raise ValueError("active_requests must be a non-negative integer")
        object.__setattr__(self, "last_activity_at", _utc(self.last_activity_at, "last_activity_at"))
        if self.shutdown_requested_at is not None:
            object.__setattr__(
                self,
                "shutdown_requested_at",
                _utc(self.shutdown_requested_at, "shutdown_requested_at"),
            )


@dataclass(frozen=True)
class LifecycleDecision:
    action: LifecycleAction
    reason: str


def decide_lifecycle(
    observation: LifecycleObservation, *, now: datetime, policy: LifecyclePolicy
) -> LifecycleDecision:
    """Return a fail-closed decision without observing or mutating processes."""

    checked_at = _utc(now, "now")
    if not observation.managed or not observation.owner_matches:
        return LifecycleDecision("report-only", "ownership-unproven")
    if observation.active_requests > 0:
        return LifecycleDecision("keep", "request-active")
    if observation.shutdown_requested_at is not None:
        if checked_at - observation.shutdown_requested_at >= policy.grace_timeout:
            return LifecycleDecision("terminate-managed-job", "grace-timeout")
        return LifecycleDecision("keep", "graceful-shutdown-in-progress")
    if not observation.owner_alive:
        return LifecycleDecision("graceful-shutdown", "owner-ended")
    if checked_at - observation.last_activity_at >= policy.idle_timeout:
        return LifecycleDecision("graceful-shutdown", "stdio-idle-timeout")
    return LifecycleDecision("keep", "connection-recently-active")
