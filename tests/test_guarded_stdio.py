import io
import json
import os
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from runtime_process_guard.admission import Observation
from runtime_process_guard.guarded_stdio import (
    GuardMode,
    _relay_output,
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
        lambda *_args, **_kwargs: replace(
            healthy_observation(), available_memory_mb=0
        ),
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
