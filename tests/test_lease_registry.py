from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import multiprocessing
from pathlib import Path

import pytest

from runtime_process_guard.lease import LeaseRecord
from runtime_process_guard.lease_registry import (
    LeaseConflictError,
    LeaseRegistry,
    LeaseRegistryLockedError,
    LeaseRegistryMalformedError,
)


NOW = datetime(2026, 8, 29, 3, 0, tzinfo=timezone.utc)


def make_lease(suffix: str, **overrides: object) -> LeaseRecord:
    values: dict[str, object] = {
        "lease_id": f"lease_{suffix}1234",
        "owner_identity": f"owner_{suffix}1234",
        "server_identity": "server_obsidian",
        "owner_pid": 1234,
        "owner_created_at": NOW - timedelta(minutes=1),
        "acquired_at": NOW,
        "heartbeat_at": NOW,
        "expires_at": NOW + timedelta(seconds=30),
    }
    values.update(overrides)
    return LeaseRecord(**values)


def _acquire_worker(path: str, suffix: str, start, results) -> None:
    start.wait()
    try:
        LeaseRegistry(Path(path), lock_timeout_seconds=2).acquire(make_lease(suffix))
        results.put("ok")
    except Exception as exc:  # pragma: no cover - evidence returned to parent
        results.put(type(exc).__name__)


def test_acquire_preserves_multiple_dedicated_stdio_leases(tmp_path) -> None:
    path = tmp_path / "leases.json"
    registry = LeaseRegistry(path)
    first = make_lease("alpha")
    second = make_lease("bravo")

    registry.acquire(first)
    registry.acquire(second)

    assert registry.read() == (first, second)
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["schema"] == "runtime_process_guard.lease_registry.v1"
    assert len(stored["leases"]) == 2
    assert "argv" not in path.read_text(encoding="utf-8")
    assert "env" not in path.read_text(encoding="utf-8")


def test_acquire_rejects_duplicate_lease_id_without_overwriting(tmp_path) -> None:
    registry = LeaseRegistry(tmp_path / "leases.json")
    original = make_lease("alpha")
    registry.acquire(original)

    with pytest.raises(LeaseConflictError, match="lease_id"):
        registry.acquire(make_lease("alpha", owner_identity="owner_other123"))

    assert registry.read() == (original,)


def test_heartbeat_and_release_compare_lease_and_owner_identity(tmp_path) -> None:
    registry = LeaseRegistry(tmp_path / "leases.json")
    original = make_lease("alpha")
    registry.acquire(original)

    with pytest.raises(LeaseConflictError, match="owner"):
        registry.heartbeat(
            original.lease_id,
            "owner_other123",
            heartbeat_at=NOW + timedelta(seconds=10),
            expires_at=NOW + timedelta(seconds=40),
        )
    updated = registry.heartbeat(
        original.lease_id,
        original.owner_identity,
        heartbeat_at=NOW + timedelta(seconds=10),
        expires_at=NOW + timedelta(seconds=40),
    )
    assert updated.heartbeat_at == NOW + timedelta(seconds=10)

    with pytest.raises(LeaseConflictError, match="owner"):
        registry.release(original.lease_id, "owner_other123")
    released = registry.release(original.lease_id, original.owner_identity)
    assert released == updated
    assert registry.read() == ()


def test_missing_lease_is_a_compare_and_swap_conflict(tmp_path) -> None:
    registry = LeaseRegistry(tmp_path / "leases.json")

    with pytest.raises(LeaseConflictError, match="not found"):
        registry.release("lease_missing1", "owner_missing1")


@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        '{"schema":"wrong","leases":[]}',
        '{"schema":"runtime_process_guard.lease_registry.v1","leases":{}}',
        '{"schema":"runtime_process_guard.lease_registry.v1","leases":[{}]}',
    ],
)
def test_malformed_registry_fails_closed(tmp_path, payload: str) -> None:
    path = tmp_path / "leases.json"
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(LeaseRegistryMalformedError):
        LeaseRegistry(path).read()


def test_existing_lock_fails_closed_without_mutation(tmp_path) -> None:
    path = tmp_path / "leases.json"
    registry = LeaseRegistry(path)
    original = make_lease("alpha")
    registry.acquire(original)
    before = path.read_bytes()
    contender = LeaseRegistry(path, lock_timeout_seconds=0)
    with registry._exclusive_lock():
        with pytest.raises(LeaseRegistryLockedError):
            contender.acquire(make_lease("bravo"))

    assert path.read_bytes() == before


def test_leftover_lock_file_is_not_a_crash_poison(tmp_path) -> None:
    path = tmp_path / "leases.json"
    path.with_name(path.name + ".lock").write_text("0", encoding="ascii")
    lease = make_lease("alpha")

    LeaseRegistry(path).acquire(lease)

    assert LeaseRegistry(path).read() == (lease,)


def test_two_processes_preserve_both_dedicated_stdio_leases(tmp_path) -> None:
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    path = tmp_path / "leases.json"
    workers = [
        context.Process(
            target=_acquire_worker,
            args=(str(path), suffix, start, results),
        )
        for suffix in ("alpha", "bravo")
    ]
    for worker in workers:
        worker.start()
    start.set()
    for worker in workers:
        worker.join(timeout=10)
        assert worker.exitcode == 0

    assert sorted(results.get(timeout=2) for _ in workers) == ["ok", "ok"]
    assert len(LeaseRegistry(path).read()) == 2
