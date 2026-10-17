from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import multiprocessing
from pathlib import Path

import pytest

from runtime_process_guard.lease import LeaseRecord, OwnerObservation
from runtime_process_guard.lease_registry import (
    LeaseConflictError,
    LeaseCapacityError,
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
    monkeypatch.setattr(
        Path, "is_symlink", lambda candidate: candidate == path or original(candidate)
    )
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


def alive_owner(lease):
    return OwnerObservation(lease.owner_pid, lease.owner_created_at, True)


def budget_registry(path):
    return LeaseRegistry(path, owner_observer=alive_owner)


def acquire_budget(registry, lease, **limits):
    registry.acquire(
        lease, **(limits or {"max_server_instances": 2, "max_total_instances": 4})
    )


def test_server_and_total_capacity_are_independent_and_do_not_overwrite(tmp_path):
    registry = budget_registry(tmp_path / "leases.json")
    for suffix in ("alpha", "bravo"):
        acquire_budget(registry, make_lease(suffix))
    before = registry.path.read_bytes()
    with pytest.raises(LeaseCapacityError):
        acquire_budget(registry, make_lease("charlie"))
    assert registry.path.read_bytes() == before
    for suffix in ("charlie", "delta"):
        acquire_budget(registry, make_lease(suffix, server_identity="server_other123"))
    before = registry.path.read_bytes()
    with pytest.raises(LeaseCapacityError):
        acquire_budget(registry, make_lease("echo", server_identity="server_third123"))
    assert registry.path.read_bytes() == before


def test_budget_metadata_survives_heartbeat_release_and_blocks_legacy_bypass(tmp_path):
    registry = budget_registry(tmp_path / "leases.json")
    original = make_lease("alpha")
    acquire_budget(registry, original)
    updated = registry.heartbeat(
        original.lease_id,
        original.owner_identity,
        heartbeat_at=NOW + timedelta(seconds=1),
        expires_at=NOW + timedelta(seconds=31),
    )
    registry.release(updated.lease_id, updated.owner_identity)
    payload = json.loads(registry.path.read_text())
    assert payload["schema"] == "runtime_process_guard.lease_registry.v2"
    assert payload["capacity"] == {"max_server_instances": 2, "max_total_instances": 4}
    before = registry.path.read_bytes()
    with pytest.raises(LeaseConflictError):
        registry.acquire(make_lease("bravo"))
    with pytest.raises(LeaseConflictError):
        acquire_budget(
            registry, make_lease("bravo"), max_server_instances=1, max_total_instances=4
        )
    assert registry.path.read_bytes() == before


def test_expired_alive_owner_keeps_slot_but_dead_or_replaced_can_be_reclaimed(tmp_path):
    path = tmp_path / "leases.json"
    legacy = LeaseRegistry(path)
    first, second = make_lease("alpha"), make_lease("bravo")
    legacy.acquire(first)
    legacy.acquire(second)
    registry = budget_registry(path)
    with pytest.raises(LeaseCapacityError):
        acquire_budget(registry, make_lease("charlie"))
    assert json.loads(path.read_text())["schema"].endswith("v1")

    def observer(lease):
        if lease.lease_id == first.lease_id:
            return OwnerObservation(lease.owner_pid, lease.owner_created_at, False)
        if lease.lease_id == second.lease_id:
            return OwnerObservation(
                lease.owner_pid, lease.owner_created_at + timedelta(seconds=1), True
            )
        return alive_owner(lease)

    registry.owner_observer = observer
    third = make_lease("charlie")
    acquire_budget(registry, third)
    assert registry.read() == (third,)
    assert json.loads(path.read_text())["schema"].endswith("v2")


def test_unknown_existing_or_incoming_owner_fails_without_mutation(tmp_path):
    path = tmp_path / "leases.json"
    original = make_lease("alpha")
    LeaseRegistry(path).acquire(original)
    registry = LeaseRegistry(path, owner_observer=lambda lease: None)
    before = path.read_bytes()
    with pytest.raises(LeaseRegistryError, match="unknown") as error:
        acquire_budget(registry, make_lease("bravo"))
    assert type(error.value) is LeaseRegistryError
    assert path.read_bytes() == before
    empty = LeaseRegistry(tmp_path / "empty.json", owner_observer=lambda lease: None)
    with pytest.raises(LeaseRegistryError) as error:
        acquire_budget(empty, original)
    assert type(error.value) is LeaseRegistryError
    assert not empty.path.exists()


@pytest.mark.parametrize(
    "server,total", [(None, 4), (2, None), (True, 4), (0, 4), (3, 2), (2, 4.0)]
)
def test_invalid_limits_do_not_write_registry(tmp_path, server, total):
    registry = budget_registry(tmp_path / "leases.json")
    with pytest.raises(ValueError):
        registry.acquire(
            make_lease("alpha"), max_server_instances=server, max_total_instances=total
        )
    assert not registry.path.exists()


@pytest.mark.parametrize(
    "capacity",
    [
        None,
        {},
        {"max_server_instances": True, "max_total_instances": 4},
        {"max_server_instances": 2, "max_total_instances": 1},
    ],
)
def test_v2_requires_valid_capacity(tmp_path, capacity):
    path = tmp_path / "leases.json"
    value = {"schema": "runtime_process_guard.lease_registry.v2", "leases": []}
    if capacity is not None:
        value["capacity"] = capacity
    path.write_text(json.dumps(value))
    with pytest.raises(LeaseRegistryMalformedError):
        LeaseRegistry(path).read()


def _budget_worker(path, suffix, start, results, server_identity="server_obsidian"):
    start.wait()
    try:
        acquire_budget(
            LeaseRegistry(
                Path(path), lock_timeout_seconds=5, owner_observer=alive_owner
            ),
            make_lease(suffix, server_identity=server_identity),
        )
        results.put("ok")
    except LeaseCapacityError:
        results.put("capacity")


def test_concurrent_reservations_never_exceed_server_capacity(tmp_path):
    context = multiprocessing.get_context("spawn")
    start, results = context.Event(), context.Queue()
    path = tmp_path / "leases.json"
    workers = [
        context.Process(target=_budget_worker, args=(str(path), suffix, start, results))
        for suffix in ("alpha", "bravo", "charlie", "delta")
    ]
    for worker in workers:
        worker.start()
    start.set()
    for worker in workers:
        worker.join(timeout=15)
        assert worker.exitcode == 0
    assert sorted(results.get(timeout=2) for _ in workers) == [
        "capacity",
        "capacity",
        "ok",
        "ok",
    ]
    assert len(LeaseRegistry(path).read()) == 2


def test_default_observer_distinguishes_access_denied_from_no_such_process(monkeypatch):
    import psutil
    from runtime_process_guard.lease_registry import _observe_owner

    lease = make_lease("alpha")

    def denied(pid):
        raise psutil.AccessDenied(pid)

    monkeypatch.setattr(psutil, "Process", denied)
    assert _observe_owner(lease) is None

    def gone(pid):
        raise psutil.NoSuchProcess(pid)

    monkeypatch.setattr(psutil, "Process", gone)
    assert _observe_owner(lease) == OwnerObservation(
        lease.owner_pid, lease.owner_created_at, False
    )


def test_concurrent_different_servers_never_exceed_total_capacity(tmp_path):
    context = multiprocessing.get_context("spawn")
    start, results = context.Event(), context.Queue()
    path = tmp_path / "leases.json"
    workers = [
        context.Process(
            target=_budget_worker,
            args=(str(path), suffix, start, results, f"server_{suffix}123"),
        )
        for suffix in ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot")
    ]
    for worker in workers:
        worker.start()
    start.set()
    for worker in workers:
        worker.join(timeout=15)
        assert worker.exitcode == 0
    assert sorted(results.get(timeout=2) for _ in workers) == [
        "capacity",
        "capacity",
        "ok",
        "ok",
        "ok",
        "ok",
    ]
    assert len(LeaseRegistry(path).read()) == 4


def test_budget_lock_contention_preserves_reservations(tmp_path):
    path = tmp_path / "leases.json"
    registry = budget_registry(path)
    acquire_budget(registry, make_lease("alpha"))
    before = path.read_bytes()
    contender = LeaseRegistry(path, lock_timeout_seconds=0, owner_observer=alive_owner)
    with registry._exclusive_lock():
        with pytest.raises(LeaseRegistryLockedError):
            acquire_budget(contender, make_lease("bravo"))
    assert path.read_bytes() == before


@pytest.mark.parametrize("after_lock", [True, False])
def test_heartbeat_deadline_checked_after_lock_and_before_write(
    tmp_path, monkeypatch, after_lock
):
    from contextlib import contextmanager
    import runtime_process_guard.lease_registry as module

    registry = LeaseRegistry(tmp_path / "leases.json")
    lease = make_lease("alpha")
    registry.acquire(lease)
    before = registry.path.read_bytes()
    deadline = NOW + timedelta(seconds=30)
    observed = [NOW]

    class Clock:
        @staticmethod
        def now(tz):
            return observed[0]

    monkeypatch.setattr(module, "datetime", Clock)
    original_lock = registry._exclusive_lock

    @contextmanager
    def waited_lock():
        with original_lock():
            if after_lock:
                observed[0] = deadline
            yield

    monkeypatch.setattr(registry, "_exclusive_lock", waited_lock)
    original_find = registry._find_owned

    def slow_find(*args):
        found = original_find(*args)
        if not after_lock:
            observed[0] = deadline
        return found

    monkeypatch.setattr(registry, "_find_owned", slow_find)
    with pytest.raises(LeaseRegistryError, match="deadline"):
        registry.heartbeat(
            lease.lease_id,
            lease.owner_identity,
            heartbeat_at=NOW + timedelta(seconds=10),
            expires_at=NOW + timedelta(seconds=40),
            renewal_deadline=deadline,
        )
    assert registry.path.read_bytes() == before


def test_heartbeat_valid_renewal_deadline_can_update(tmp_path, monkeypatch):
    import runtime_process_guard.lease_registry as module

    registry = budget_registry(tmp_path / "leases.json")
    lease = make_lease("alpha")
    acquire_budget(registry, lease)

    class Clock:
        @staticmethod
        def now(tz):
            return NOW

    monkeypatch.setattr(module, "datetime", Clock)
    updated = registry.heartbeat(
        lease.lease_id,
        lease.owner_identity,
        heartbeat_at=NOW + timedelta(seconds=10),
        expires_at=NOW + timedelta(seconds=40),
        renewal_deadline=lease.expires_at,
    )
    assert updated.expires_at == NOW + timedelta(seconds=40)
    assert json.loads(registry.path.read_text())["schema"].endswith("v2")
