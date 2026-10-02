"""Atomic, privacy-safe storage for dedicated stdio runtime leases."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Callable, Iterator, Mapping

from .lease import LeaseRecord, OwnerObservation, classify_lease
from .plugin_window_policy import _is_link_like


_SCHEMA = "runtime_process_guard.lease_registry.v1"
_BUDGET_SCHEMA = "runtime_process_guard.lease_registry.v2"


class LeaseRegistryError(RuntimeError):
    """Base class for fail-closed registry failures."""


class LeaseRegistryLockedError(LeaseRegistryError):
    """The registry cannot be mutated because its exclusive lock is held."""


class LeaseRegistryMalformedError(LeaseRegistryError):
    """The persisted registry cannot be safely interpreted."""


class LeaseConflictError(LeaseRegistryError):
    """A compare-and-swap precondition did not match persisted state."""


class LeaseCapacityError(LeaseRegistryError):
    """A shared capacity contract or available launch slot was not satisfied."""


class LeaseRegistry:
    """Persist multiple leases using an exclusive lock and atomic replacement.

    Only ``LeaseRecord`` fields and numeric capacity limits are serialized.
    In particular, command lines,
    paths, and environment variables are not accepted by this API.
    """

    def __init__(
        self,
        path: Path,
        *,
        lock_timeout_seconds: float = 0.5,
        owner_observer: Callable[[LeaseRecord], OwnerObservation | None] | None = None,
    ) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self.lock_timeout_seconds = lock_timeout_seconds
        self.owner_observer = owner_observer or _observe_owner

    def read(self) -> tuple[LeaseRecord, ...]:
        return self._read_unlocked()

    def acquire(
        self,
        lease: LeaseRecord,
        *,
        max_server_instances: int | None = None,
        max_total_instances: int | None = None,
    ) -> None:
        requested = _capacity(max_server_instances, max_total_instances)
        with self._exclusive_lock():
            current_leases, persisted = self._read_state_unlocked()
            leases = list(current_leases)
            if persisted is not None and requested != persisted:
                raise LeaseConflictError("shared capacity contract does not match")
            if requested is not None:
                now = datetime.now(timezone.utc)
                retained = []
                for current in leases:
                    status = classify_lease(
                        current, now=now, owner=self.owner_observer(current)
                    )
                    if status.status == "unknown-owner":
                        raise LeaseRegistryError("lease owner observation is unknown")
                    if not status.safe_to_reclaim:
                        retained.append(current)
                leases = retained
                incoming = classify_lease(
                    lease, now=now, owner=self.owner_observer(lease)
                )
                if incoming.status == "unknown-owner" or incoming.safe_to_reclaim:
                    raise LeaseRegistryError("new lease owner is not confirmed alive")
            if any(current.lease_id == lease.lease_id for current in leases):
                raise LeaseConflictError("lease_id already exists")
            if requested is not None:
                server_count = sum(
                    current.server_identity == lease.server_identity
                    for current in leases
                )
                if (
                    server_count >= requested["max_server_instances"]
                    or len(leases) >= requested["max_total_instances"]
                ):
                    raise LeaseCapacityError("shared lease capacity reached")
            leases.append(lease)
            self._write_unlocked(leases, capacity=requested)

    def heartbeat(
        self,
        lease_id: str,
        owner_identity: str,
        *,
        heartbeat_at: datetime,
        expires_at: datetime,
        renewal_deadline: datetime | None = None,
    ) -> LeaseRecord:
        if renewal_deadline is not None and (
            renewal_deadline.tzinfo is None or renewal_deadline.utcoffset() is None
        ):
            raise ValueError("renewal_deadline must be timezone-aware")
        with self._exclusive_lock():
            _check_renewal_deadline(renewal_deadline)
            records, capacity = self._read_state_unlocked()
            leases = list(records)
            index, current = self._find_owned(leases, lease_id, owner_identity)
            if heartbeat_at < current.heartbeat_at:
                raise LeaseConflictError("heartbeat_at must not move backwards")
            updated = replace(
                current,
                heartbeat_at=heartbeat_at,
                expires_at=expires_at,
            )
            leases[index] = updated
            _check_renewal_deadline(renewal_deadline)
            self._write_unlocked(leases, capacity=capacity)
            return updated

    def release(self, lease_id: str, owner_identity: str) -> LeaseRecord:
        with self._exclusive_lock():
            records, capacity = self._read_state_unlocked()
            leases = list(records)
            index, current = self._find_owned(leases, lease_id, owner_identity)
            del leases[index]
            self._write_unlocked(leases, capacity=capacity)
            return current

    @staticmethod
    def _find_owned(
        leases: list[LeaseRecord], lease_id: str, owner_identity: str
    ) -> tuple[int, LeaseRecord]:
        for index, lease in enumerate(leases):
            if lease.lease_id != lease_id:
                continue
            if lease.owner_identity != owner_identity:
                raise LeaseConflictError("lease owner identity does not match")
            return index, lease
        raise LeaseConflictError(f"lease not found: {lease_id}")

    def _read_unlocked(self) -> tuple[LeaseRecord, ...]:
        return self._read_state_unlocked()[0]

    def _read_state_unlocked(
        self,
    ) -> tuple[tuple[LeaseRecord, ...], dict[str, int] | None]:
        self._assert_path_safe()
        if not self.path.exists():
            return (), None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, Mapping):
                raise ValueError("registry root must be an object")
            if raw.get("schema") not in (_SCHEMA, _BUDGET_SCHEMA):
                raise ValueError("unsupported registry schema")
            values = raw.get("leases")
            if not isinstance(values, list):
                raise ValueError("leases must be a list")
            leases = tuple(
                (
                    LeaseRecord.from_dict(value)
                    if isinstance(value, Mapping)
                    else _raise_value_error("lease must be an object")
                )
                for value in values
            )
            ids = [lease.lease_id for lease in leases]
            if len(ids) != len(set(ids)):
                raise ValueError("duplicate lease_id in registry")
            capacity = None
            if "capacity" in raw:
                value = raw["capacity"]
                if not isinstance(value, Mapping) or set(value) != {
                    "max_server_instances",
                    "max_total_instances",
                }:
                    raise ValueError("invalid capacity contract")
                capacity = _capacity(
                    value["max_server_instances"], value["max_total_instances"]
                )
                if capacity is None:
                    raise ValueError("empty capacity contract")
            if raw["schema"] == _BUDGET_SCHEMA and capacity is None:
                raise ValueError("capacity required for budget registry")
            return leases, capacity
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            ValueError,
            TypeError,
        ) as exc:
            raise LeaseRegistryMalformedError(
                f"lease registry is unreadable or malformed: {self.path.name}"
            ) from exc

    def _write_unlocked(
        self,
        leases: list[LeaseRecord],
        *,
        capacity: dict[str, int] | None = None,
    ) -> None:
        self._assert_path_safe()
        payload = {
            "schema": _BUDGET_SCHEMA if capacity is not None else _SCHEMA,
            "leases": [lease.to_dict() for lease in leases],
        }
        if capacity is not None:
            payload["capacity"] = capacity
        temporary_path: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(
                prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
            )
            temporary_path = Path(name)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=True, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            self._assert_path_safe()
            os.replace(temporary_path, self.path)
            temporary_path = None
        except OSError as exc:
            raise LeaseRegistryError(
                f"lease registry could not be atomically persisted: {self.path.name}"
            ) from exc
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        self._assert_path_safe()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise LeaseRegistryError("lease registry parent is inaccessible") from exc
        self._assert_path_safe(lock_path=True)
        try:
            descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        except OSError as exc:
            raise LeaseRegistryError(
                f"lease registry lock could not be acquired: {self.lock_path.name}"
            ) from exc
        try:
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"0")
                os.fsync(descriptor)
            deadline = time.monotonic() + self.lock_timeout_seconds
            while True:
                try:
                    _lock_descriptor(descriptor)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise LeaseRegistryLockedError(
                            f"lease registry lock is busy: {self.lock_path.name}"
                        ) from exc
                    time.sleep(0.01)
            yield
        finally:
            try:
                _unlock_descriptor(descriptor)
            except OSError:
                pass
            os.close(descriptor)

    def _assert_path_safe(self, *, lock_path: bool = False) -> None:
        """Reject registry and lock paths below symlink/reparse ancestors."""
        target = self.lock_path if lock_path else self.path
        current = target
        while True:
            if current.is_symlink() or (current.exists() and _is_link_like(current)):
                kind = "lock" if lock_path else "registry"
                raise LeaseRegistryMalformedError(
                    f"lease {kind} path must not contain a symlink or reparse point"
                )
            parent = current.parent
            if parent == current:
                return
            current = parent


def _raise_value_error(message: str) -> LeaseRecord:
    raise ValueError(message)


def _lock_descriptor(descriptor: int) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_descriptor(descriptor: int) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_UN)


def _capacity(server: int | None, total: int | None) -> dict[str, int] | None:
    if server is None and total is None:
        return None
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value <= 0
        for value in (server, total)
    ):
        raise ValueError("both capacity limits must be positive integers")
    if server > total:
        raise ValueError("server capacity must not exceed total capacity")
    return {"max_server_instances": server, "max_total_instances": total}


def _observe_owner(lease: LeaseRecord) -> OwnerObservation | None:
    import psutil

    try:
        process = psutil.Process(lease.owner_pid)
        created = datetime.fromtimestamp(process.create_time(), timezone.utc)
        return OwnerObservation(lease.owner_pid, created, process.is_running())
    except psutil.NoSuchProcess:
        return OwnerObservation(lease.owner_pid, lease.owner_created_at, False)
    except (psutil.AccessDenied, OSError):
        return None


def _check_renewal_deadline(deadline: datetime | None) -> None:
    if deadline is not None and datetime.now(timezone.utc) >= deadline:
        raise LeaseRegistryError("lease renewal deadline has elapsed")
