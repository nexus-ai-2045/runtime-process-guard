import os
from pathlib import Path
import subprocess
import sys

import pytest

from runtime_process_guard.guarded_stdio import GuardMode, validate_guard_options


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
def test_windows_shadow_round_trip_and_eof_cleanup() -> None:
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
            "--",
            sys.executable,
            "-c",
            "import sys; data=sys.stdin.buffer.read(); sys.stdout.buffer.write(data)",
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
