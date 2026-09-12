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
    LeaseRegistryError,
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


def test_parent_directory_failure_is_normalized(tmp_path, monkeypatch) -> None:
    registry = LeaseRegistry(tmp_path / "missing" / "leases.json")
    original_mkdir = Path.mkdir

    def fail_target_parent(path: Path, *args, **kwargs) -> None:
        if path == registry.path.parent:
            raise OSError("synthetic inaccessible parent")
        original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail_target_parent)

    with pytest.raises(LeaseRegistryError) as error:
        registry.acquire(make_lease("alpha"))

    assert "missing" not in str(error.value)
    assert str(tmp_path) not in str(error.value)


@pytest.mark.parametrize("dangling", [False, True])
def test_symlink_registry_is_rejected_without_replacement(tmp_path, dangling) -> None:
    target = tmp_path / "target.json"
    if not dangling:
        LeaseRegistry(target).acquire(make_lease("alpha"))
    before = target.read_bytes() if target.exists() else None
    link = tmp_path / "link.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation unavailable")
    registry = LeaseRegistry(link)
    for operation in (registry.read, lambda: registry.acquire(make_lease("bravo"))):
        with pytest.raises(LeaseRegistryMalformedError, match="symlink"):
            operation()
    assert link.is_symlink()
    assert (target.read_bytes() if target.exists() else None) == before


def test_registry_symlink_gate_precedes_read_and_write(tmp_path, monkeypatch) -> None:
    path = tmp_path / "leases.json"
    registry = LeaseRegistry(path)
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda candidate: candidate == path or original(candidate))
    with pytest.raises(LeaseRegistryMalformedError, match="symlink"):
        registry.read()
    with pytest.raises(LeaseRegistryMalformedError, match="symlink"):
        registry._write_unlocked([make_lease("alpha")])
    assert not path.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_registry_rejects_symlinked_parent_before_lock_or_write(tmp_path) -> None:
    target = tmp_path / "real"
    target.mkdir()
    parent_link = tmp_path / "parent-link"
    try:
        parent_link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable")
    registry = LeaseRegistry(parent_link / "leases.json")
    for operation in (registry.read, lambda: registry.acquire(make_lease("alpha"))):
        with pytest.raises(LeaseRegistryMalformedError, match="symlink or reparse"):
            operation()
    assert not (target / "leases.json").exists()
    assert not (target / "leases.json.lock").exists()
