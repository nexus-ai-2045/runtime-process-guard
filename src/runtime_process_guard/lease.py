"""Privacy-safe runtime lease records and pure stale classification."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Literal, Mapping


LeaseStatus = Literal[
    "active",
    "expired-owner-alive",
    "stale-owner-dead",
    "stale-owner-replaced",
    "unknown-owner",
]

_IDENTITY_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{7,127}$")


def _require_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _format_time(value: datetime) -> str:
    return _require_utc(value, "timestamp").isoformat().replace("+00:00", "Z")


def _parse_time(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO 8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO 8601 string") from exc
    return _require_utc(parsed, field)


@dataclass(frozen=True)
class LeaseRecord:
    lease_id: str
    owner_identity: str
    server_identity: str
    owner_pid: int
    owner_created_at: datetime
    acquired_at: datetime
    heartbeat_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        for field in ("lease_id", "owner_identity", "server_identity"):
            value = getattr(self, field)
            if not isinstance(value, str) or not _IDENTITY_PATTERN.fullmatch(value):
                raise ValueError(f"{field} must be an anonymous stable identity")
        if not isinstance(self.owner_pid, int) or isinstance(self.owner_pid, bool) or self.owner_pid <= 0:
            raise ValueError("owner_pid must be a positive integer")
        for field in ("owner_created_at", "acquired_at", "heartbeat_at", "expires_at"):
            object.__setattr__(self, field, _require_utc(getattr(self, field), field))
        if self.heartbeat_at < self.acquired_at:
            raise ValueError("heartbeat_at must not precede acquired_at")
        if self.expires_at < self.heartbeat_at:
            raise ValueError("expires_at must not precede heartbeat_at")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "runtime_process_guard.lease.v1",
            "lease_id": self.lease_id,
            "owner_identity": self.owner_identity,
            "server_identity": self.server_identity,
            "owner_pid": self.owner_pid,
            "owner_created_at": _format_time(self.owner_created_at),
            "acquired_at": _format_time(self.acquired_at),
            "heartbeat_at": _format_time(self.heartbeat_at),
            "expires_at": _format_time(self.expires_at),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "LeaseRecord":
        if value.get("schema") != "runtime_process_guard.lease.v1":
            raise ValueError("unsupported lease schema")
        try:
            owner_pid = value["owner_pid"]
            if not isinstance(owner_pid, int) or isinstance(owner_pid, bool):
                raise ValueError("owner_pid must be a positive integer")
            return cls(
                lease_id=str(value["lease_id"]),
                owner_identity=str(value["owner_identity"]),
                server_identity=str(value["server_identity"]),
                owner_pid=owner_pid,
                owner_created_at=_parse_time(value["owner_created_at"], "owner_created_at"),
                acquired_at=_parse_time(value["acquired_at"], "acquired_at"),
                heartbeat_at=_parse_time(value["heartbeat_at"], "heartbeat_at"),
                expires_at=_parse_time(value["expires_at"], "expires_at"),
            )
        except KeyError as exc:
            raise ValueError(f"missing lease field: {exc.args[0]}") from exc


@dataclass(frozen=True)
class OwnerObservation:
    pid: int
    created_at: datetime
    alive: bool

    def __post_init__(self) -> None:
        if not isinstance(self.pid, int) or isinstance(self.pid, bool) or self.pid <= 0:
            raise ValueError("pid must be a positive integer")
        object.__setattr__(self, "created_at", _require_utc(self.created_at, "created_at"))


@dataclass(frozen=True)
class LeaseClassification:
    status: LeaseStatus
    safe_to_reclaim: bool


def classify_lease(
    lease: LeaseRecord,
    *,
    now: datetime,
    owner: OwnerObservation | None,
) -> LeaseClassification:
    """Classify a lease without reading processes or mutating runtime state."""

    checked_at = _require_utc(now, "now")
    if owner is None or owner.pid != lease.owner_pid:
        return LeaseClassification("unknown-owner", False)
    if owner.created_at != lease.owner_created_at:
        return LeaseClassification("stale-owner-replaced", True)
    if not owner.alive:
        return LeaseClassification("stale-owner-dead", True)
    if checked_at >= lease.expires_at:
        return LeaseClassification("expired-owner-alive", False)
    return LeaseClassification("active", False)
