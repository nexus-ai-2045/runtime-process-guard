import io
import json
import os
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import threading
import queue
from datetime import datetime, timezone

import psutil

import pytest

from runtime_process_guard.admission import Observation
from runtime_process_guard.guarded_stdio import (
    GuardMode,
    _receipt,
    _relay_output,
    _settle_owned_job,
    run_guarded_stdio,
    validate_guard_options,
)
from runtime_process_guard.lease_registry import LeaseRegistry


def test_shadow_mode_never_enables_idle_enforcement() -> None:
    options = validate_guard_options(
        mode=GuardMode.SHADOW,
        idle_seconds=600,
        grace_seconds=30,
        command=["python", "server.py"],
    )
    assert options.enforcement_enabled is False


def test_enforce_fails_closed_until_active_requests_are_tracked() -> None:
    try:
        validate_guard_options(
            mode=GuardMode.ENFORCE,
            idle_seconds=600,
            grace_seconds=30,
            command=["python", "server.py"],
        )
    except ValueError as exc:
        assert "active JSON-RPC requests" in str(exc)
    else:
        raise AssertionError("enforce must fail closed")


def test_non_finite_timeouts_are_rejected() -> None:
    for value in (float("nan"), float("inf")):
        try:
            validate_guard_options(
                mode=GuardMode.SHADOW,
                idle_seconds=value,
                grace_seconds=30,
                command=["python", "server.py"],
            )
        except ValueError:
            pass
        else:
            raise AssertionError("non-finite timeout was accepted")


def test_command_is_required_and_shell_text_is_not_accepted() -> None:
    for command in ([], "python server.py"):
        try:
            validate_guard_options(
                mode=GuardMode.SHADOW,
                idle_seconds=600,
                grace_seconds=30,
                command=command,
            )
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError("unsafe command representation was accepted")


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object integration")
def test_windows_shadow_round_trip_and_eof_cleanup(tmp_path) -> None:
    repo = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(repo / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "runtime_process_guard.cli",
            "guarded-stdio",
            "--mode",
            "shadow",
            "--grace-seconds",
            "2",
            "--lease-state",
            str(tmp_path / "leases.json"),
            "--min-available-memory-mb",
            "0",
            "--max-cpu-percent",
            "1000",
            "--",
            sys.executable,
            "-c",
            "import sys; sys.stderr.write('secret-path-value'); data=sys.stdin.buffer.read(); sys.stdout.buffer.write(data)",
        ],
        input=b"small-frame\n",
        capture_output=True,
        timeout=10,
        env=environment,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout == b"small-frame\n"
    assert b'"managed_job": true' in result.stderr
    assert b"secret-path-value" not in result.stderr
    assert b'"event": "admission"' in result.stderr
    assert b'"event": "postflight"' in result.stderr
    stored = json.loads((tmp_path / "leases.json").read_text(encoding="utf-8"))
    assert stored["leases"] == []


def test_child_stdout_eof_marks_relay_failure() -> None:
    failed = threading.Event()
    _relay_output(io.BytesIO(), io.BytesIO(), failed)
    assert failed.is_set()


def test_receipt_failure_is_non_throwing(monkeypatch) -> None:
    class ClosedStderr:
        def write(self, _value):
            raise BrokenPipeError("closed")

        def flush(self):
            raise BrokenPipeError("closed")

    monkeypatch.setattr(sys, "stderr", ClosedStderr())

    assert _receipt("started") is False


def test_postflight_terminates_owned_descendants_before_final_status(
    monkeypatch,
) -> None:
    statuses = iter(("still-running", "gone"))
    monkeypatch.setattr(
        "runtime_process_guard.guarded_stdio.verify_owned_disappearance",
        lambda *_args, **_kwargs: next(statuses),
    )

    class FakeJob:
        terminate_count = 0

        def active_process_count(self):
            return 1

        def terminate(self):
            self.terminate_count += 1

    job = FakeJob()

    assert _settle_owned_job(job, 1) == "gone"
    assert job.terminate_count == 1


def healthy_observation() -> Observation:
    return Observation(
        identity="a" * 64,
        executable="python.exe",
        duplicate_pids=(),
        available_memory_mb=8192,
        cpu_percent=1.0,
        reuse_policy="dedicated-stdio",
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows guarded launcher")
def test_admission_defer_does_not_launch_or_create_lease(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "runtime_process_guard.guarded_stdio.observe",
        lambda *_args, **_kwargs: replace(healthy_observation(), available_memory_mb=0),
    )
    monkeypatch.setattr(
        "runtime_process_guard.guarded_stdio.subprocess.Popen",
        lambda *_args, **_kwargs: pytest.fail("Popen must not be called"),
    )
    path = tmp_path / "leases.json"
    options = validate_guard_options(
        mode=GuardMode.SHADOW,
        idle_seconds=600,
        grace_seconds=1,
        command=[sys.executable, "-c", "pass"],
        lease_state=path,
    )
    assert run_guarded_stdio(options) == 20
    assert not path.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows guarded launcher")
def test_lease_is_persisted_before_process_creation(tmp_path, monkeypatch) -> None:
    path = tmp_path / "leases.json"
    monkeypatch.setattr(
        "runtime_process_guard.guarded_stdio.observe",
        lambda *_args, **_kwargs: healthy_observation(),
    )

    def fail_after_check(*_args, **_kwargs):
        assert len(LeaseRegistry(path).read()) == 1
        raise RuntimeError("synthetic launch failure")

    monkeypatch.setattr(
        "runtime_process_guard.guarded_stdio.subprocess.Popen", fail_after_check
    )
    options = validate_guard_options(
        mode=GuardMode.SHADOW,
        idle_seconds=600,
        grace_seconds=1,
        command=[sys.executable, "-c", "pass"],
        lease_state=path,
        min_available_memory_mb=0,
    )
    with pytest.raises(RuntimeError, match="synthetic launch failure"):
        run_guarded_stdio(options)
    assert LeaseRegistry(path).read() == ()


@pytest.mark.skipif(os.name != "nt", reason="Windows guarded launcher")
def test_failed_launch_retains_lease_when_disappearance_is_unknown(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "leases.json"
    monkeypatch.setattr(
        "runtime_process_guard.guarded_stdio.observe",
        lambda *_args, **_kwargs: healthy_observation(),
    )

    class StuckChild:
        pid = 4321

        def poll(self):
            return None

        def terminate(self):
            return None

        def kill(self):
            return None

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired("child", timeout)

    class AssignmentFailureJob:
        def assign(self, _pid):
            raise OSError("synthetic assignment failure")

        def close(self):
            return None

    monkeypatch.setattr(
        "runtime_process_guard.guarded_stdio.subprocess.Popen",
        lambda *_args, **_kwargs: StuckChild(),
    )
    monkeypatch.setattr(
        "runtime_process_guard.guarded_stdio.WindowsJob",
        AssignmentFailureJob,
    )
    options = validate_guard_options(
        mode=GuardMode.SHADOW,
        idle_seconds=600,
        grace_seconds=0.1,
        command=[sys.executable, "-c", "pass"],
        lease_state=path,
        min_available_memory_mb=0,
    )

    with pytest.raises(OSError, match="synthetic assignment failure"):
        run_guarded_stdio(options)

    assert len(LeaseRegistry(path).read()) == 1


def test_live_grandchild_outlives_exited_direct_child():
    from runtime_process_guard.guarded_stdio import _owned_job_alive

    class Job:
        def active_process_count(self):
            return 1

    assert _owned_job_alive(Job()) is True


def test_unknown_job_count_is_not_disappearance():
    from runtime_process_guard.guarded_stdio import _owned_job_alive

    class Job:
        def active_process_count(self):
            raise OSError("inaccessible")

    assert _owned_job_alive(Job()) is None


@pytest.mark.parametrize(
    "error,expected",
    [(psutil.NoSuchProcess(7), "ended"), (psutil.AccessDenied(7), "unknown")],
)
def test_client_owner_missing_and_unreadable_differ(monkeypatch, error, expected):
    from runtime_process_guard.guarded_stdio import _client_owner_status

    def inaccessible(_pid):
        raise error

    monkeypatch.setattr(psutil, "Process", inaccessible)
    assert _client_owner_status(7, datetime.now(timezone.utc)) == expected


def test_client_owner_pid_reuse_is_ended(monkeypatch):
    from runtime_process_guard.guarded_stdio import _client_owner_status

    class Process:
        def create_time(self):
            return 1234.0

    monkeypatch.setattr(psutil, "Process", lambda _pid: Process())
    assert (
        _client_owner_status(7, datetime.fromtimestamp(1233, timezone.utc)) == "ended"
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object integration")
def test_windows_exited_shim_keeps_grandchild_relay_alive(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(repo / "src")
    server = (
        "import sys,time,os,json,psutil; time.sleep(2); "
        "print(json.dumps({'reply':'grandchild-response','pid':os.getpid(),"
        "'created':psutil.Process().create_time()}),flush=True); sys.stdin.buffer.read()"
    )
    shim = (
        "import subprocess,sys; "
        f"subprocess.Popen([sys.executable,'-c',{server!r}],"
        "stdin=sys.stdin.buffer,stdout=sys.stdout.buffer,stderr=subprocess.DEVNULL,"
        "creationflags=0x08000000)"
    )
    guard = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "runtime_process_guard.cli",
            "guarded-stdio",
            "--mode",
            "shadow",
            "--grace-seconds",
            "0.5",
            "--lease-state",
            str(tmp_path / "leases.json"),
            "--min-available-memory-mb",
            "0",
            "--max-cpu-percent",
            "1000",
            "--",
            sys.executable,
            "-c",
            shim,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        creationflags=0x08000000,
    )
    output = queue.Queue()
    reader = threading.Thread(
        target=lambda: output.put(guard.stdout.readline()), daemon=True
    )
    reader.start()
    try:
        try:
            response = output.get(timeout=10)
        except queue.Empty:
            guard.kill()
            _stdout, stderr = guard.communicate(timeout=5)
            pytest.fail(f"owned shim response timed out: {stderr!r}")
        observed = json.loads(response)
        assert observed["reply"] == "grandchild-response"
        assert psutil.Process(observed["pid"]).create_time() == observed["created"]
        assert guard.poll() is None
        guard.stdin.close()
        guard.stdin = None
        _stdout, stderr = guard.communicate(timeout=5)
        assert guard.returncode == 0
        assert b'"status": "gone"' in stderr
        assert LeaseRegistry(tmp_path / "leases.json").read() == ()
        # Independent process observation is separate from the Job receipt.
        try:
            survivor = psutil.Process(observed["pid"])
        except psutil.NoSuchProcess:
            survivor = None
        assert survivor is None or survivor.create_time() != observed["created"]
    finally:
        if guard.poll() is None:
            guard.kill()
            guard.communicate(timeout=5)


def test_heartbeat_retries_only_lock_contention(monkeypatch):
    from runtime_process_guard.guarded_stdio import _heartbeat_with_retry, _make_lease
    from runtime_process_guard.lease_registry import (
        LeaseRegistryLockedError,
        LeaseConflictError,
    )

    options = validate_guard_options(
        mode=GuardMode.SHADOW, idle_seconds=600, grace_seconds=30, command=["python"]
    )
    lease = _make_lease(options)

    class Registry:
        attempts = 0

        def heartbeat(self, *_args, **_kwargs):
            self.attempts += 1
            if self.attempts < 3:
                raise LeaseRegistryLockedError("busy")

    registry = Registry()
    _heartbeat_with_retry(registry, lease, 30)
    assert registry.attempts == 3

    class Conflict:
        attempts = 0

        def heartbeat(self, *_args, **_kwargs):
            self.attempts += 1
            raise LeaseConflictError("conflict")

    conflict = Conflict()
    with pytest.raises(LeaseConflictError):
        _heartbeat_with_retry(conflict, lease, 30)
    assert conflict.attempts == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_server_instances": 2},
        {"max_server_instances": True, "max_total_instances": 4},
        {"client_owner_pid": 7},
        {"client_owner_pid": 7, "client_owner_created_at": datetime.now()},
    ],
)
def test_limits_and_owner_pair_validation(kwargs):
    with pytest.raises(ValueError):
        validate_guard_options(
            mode=GuardMode.SHADOW,
            idle_seconds=600,
            grace_seconds=30,
            command=["python"],
            **kwargs,
        )


def test_heartbeat_stops_after_three_busy_locks():
    from runtime_process_guard.guarded_stdio import _heartbeat_with_retry, _make_lease
    from runtime_process_guard.lease_registry import LeaseRegistryLockedError

    lease = _make_lease(
        validate_guard_options(
            mode=GuardMode.SHADOW,
            idle_seconds=600,
            grace_seconds=30,
            command=["python"],
        )
    )

    class Busy:
        attempts = 0

        def heartbeat(self, *_args, **_kwargs):
            self.attempts += 1
            raise LeaseRegistryLockedError("busy")

    registry = Busy()
    with pytest.raises(LeaseRegistryLockedError):
        _heartbeat_with_retry(registry, lease, 30)
    assert registry.attempts == 3


def test_expired_heartbeat_does_not_retry():
    from runtime_process_guard.guarded_stdio import _heartbeat_with_retry, _make_lease
    from runtime_process_guard.lease_registry import LeaseRegistryError
    from datetime import timedelta

    lease = _make_lease(
        validate_guard_options(
            mode=GuardMode.SHADOW,
            idle_seconds=600,
            grace_seconds=30,
            command=["python"],
        )
    )
    past = lease.heartbeat_at - timedelta(seconds=2)
    lease = replace(
        lease,
        acquired_at=past,
        heartbeat_at=past,
        expires_at=past + timedelta(seconds=1),
    )

    class Registry:
        def heartbeat(self, *_args, **_kwargs):
            pytest.fail("expired heartbeat must not mutate registry")

    with pytest.raises(LeaseRegistryError):
        _heartbeat_with_retry(Registry(), lease, 30)


@pytest.mark.skipif(os.name != "nt", reason="Windows guarded launcher")
def test_capacity_rejection_does_not_launch(tmp_path, monkeypatch):
    from runtime_process_guard.lease_registry import LeaseCapacityError

    monkeypatch.setattr(
        "runtime_process_guard.guarded_stdio.observe",
        lambda *_a, **_k: healthy_observation(),
    )

    class Registry:
        def __init__(self, *_args, **_kwargs):
            pass

        def acquire(self, *_args, **kwargs):
            assert kwargs == {"max_server_instances": 2, "max_total_instances": 4}
            raise LeaseCapacityError("full")

    monkeypatch.setattr("runtime_process_guard.guarded_stdio.LeaseRegistry", Registry)
    monkeypatch.setattr(
        "runtime_process_guard.guarded_stdio.subprocess.Popen",
        lambda *_a, **_k: pytest.fail("must not launch"),
    )
    options = validate_guard_options(
        mode=GuardMode.SHADOW,
        idle_seconds=600,
        grace_seconds=30,
        command=[sys.executable],
        lease_state=tmp_path / "leases.json",
        max_server_instances=2,
        max_total_instances=4,
    )
    assert run_guarded_stdio(options) == 20
