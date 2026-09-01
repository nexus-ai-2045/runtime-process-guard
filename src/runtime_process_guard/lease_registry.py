"""Atomic, privacy-safe storage for dedicated stdio runtime leases."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Iterator, Mapping

from .lease import LeaseRecord


_SCHEMA = "runtime_process_guard.lease_registry.v1"


class LeaseRegistryError(RuntimeError):
    """Base class for fail-closed registry failures."""


class LeaseRegistryLockedError(LeaseRegistryError):
    """The registry cannot be mutated because its exclusive lock is held."""


class LeaseRegistryMalformedError(LeaseRegistryError):
    """The persisted registry cannot be safely interpreted."""


class LeaseConflictError(LeaseRegistryError):
    """A compare-and-swap precondition did not match persisted state."""


class LeaseRegistry:
    """Persist multiple leases using an exclusive lock and atomic replacement.

    Only ``LeaseRecord`` fields are serialized. In particular, command lines,
    paths, and environment variables are not accepted by this API.
    """

    def __init__(self, path: Path, *, lock_timeout_seconds: float = 0.5) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self.lock_timeout_seconds = lock_timeout_seconds

    def read(self) -> tuple[LeaseRecord, ...]:
        return self._read_unlocked()

    def acquire(self, lease: LeaseRecord) -> None:
        with self._exclusive_lock():
            leases = list(self._read_unlocked())
            if any(current.lease_id == lease.lease_id for current in leases):
                raise LeaseConflictError(f"lease_id already exists: {lease.lease_id}")
            leases.append(lease)
            self._write_unlocked(leases)

    def heartbeat(
        self,
        lease_id: str,
        owner_identity: str,
        *,
        heartbeat_at: datetime,
        expires_at: datetime,
    ) -> LeaseRecord:
        with self._exclusive_lock():
            leases = list(self._read_unlocked())
            index, current = self._find_owned(leases, lease_id, owner_identity)
            if heartbeat_at < current.heartbeat_at:
                raise LeaseConflictError("heartbeat_at must not move backwards")
            updated = replace(
                current,
                heartbeat_at=heartbeat_at,
                expires_at=expires_at,
            )
            leases[index] = updated
            self._write_unlocked(leases)
            return updated

    def release(self, lease_id: str, owner_identity: str) -> LeaseRecord:
        with self._exclusive_lock():
            leases = list(self._read_unlocked())
            index, current = self._find_owned(leases, lease_id, owner_identity)
            del leases[index]
            self._write_unlocked(leases)
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
        if not self.path.exists():
            return ()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, Mapping):
                raise ValueError("registry root must be an object")
            if raw.get("schema") != _SCHEMA:
                raise ValueError("unsupported registry schema")
            values = raw.get("leases")
            if not isinstance(values, list):
                raise ValueError("leases must be a list")
            leases = tuple(
                LeaseRecord.from_dict(value)
                if isinstance(value, Mapping)
                else _raise_value_error("lease must be an object")
                for value in values
            )
            ids = [lease.lease_id for lease in leases]
            if len(ids) != len(set(ids)):
                raise ValueError("duplicate lease_id in registry")
            return leases
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
            raise LeaseRegistryMalformedError(
                f"lease registry is unreadable or malformed: {self.path.name}"
            ) from exc

    def _write_unlocked(self, leases: list[LeaseRecord]) -> None:
        payload = {
            "schema": _SCHEMA,
            "leases": [lease.to_dict() for lease in leases],
        }
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
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise LeaseRegistryError("lease registry parent is inaccessible") from exc
        if self.lock_path.is_symlink():
            raise LeaseRegistryError("lease registry lock path must not be a symlink")
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
