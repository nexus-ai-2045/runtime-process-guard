"""Verify an installed wheel from outside the source checkout."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True, type=Path)
    args = parser.parse_args()
    executable = str(args.python.absolute())
    entrypoint_name = "runtime-process-guard.exe" if os.name == "nt" else "runtime-process-guard"
    entrypoint = str(Path(executable).with_name(entrypoint_name))
    venv_prefix = Path(executable).parent.parent.resolve()
    results: dict[str, object] = {
        "isolated_environment": False,
        "cli": False,
        "windows_owned_job": "not-applicable",
    }
    with tempfile.TemporaryDirectory(prefix="guard-wheel-") as directory:
        base = [executable, "-I", "-m", "runtime_process_guard.cli"]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            prefix_result = subprocess.run(
                [executable, "-I", "-c", "import sys; print(sys.prefix)"],
                cwd=directory, capture_output=True, text=True,
                timeout=30, creationflags=flags,
            )
            if prefix_result.returncode != 0 or Path(prefix_result.stdout.strip()).resolve() != venv_prefix:
                raise RuntimeError("interpreter is outside the isolated environment")
            results["isolated_environment"] = True
            help_result = subprocess.run(
                [*base, "--help"], cwd=directory, capture_output=True,
                timeout=30, creationflags=flags,
            )
            entry_result = subprocess.run(
                [entrypoint, "--help"], cwd=directory, capture_output=True,
                timeout=30, creationflags=flags,
            )
            if (
                help_result.returncode != 0
                or b"guarded-stdio" not in help_result.stdout
                or entry_result.returncode != 0
                or b"guarded-stdio" not in entry_result.stdout
            ):
                raise RuntimeError("installed CLI unavailable")
            results["cli"] = True
            if os.name == "nt":
                state = Path(directory) / "lease.json"
                echo = "import sys; data=sys.stdin.buffer.read(); sys.stdout.buffer.write(data); sys.stdout.buffer.flush()"
                completed = subprocess.run(
                    [*base, "guarded-stdio", "--lease-state", str(state),
                     "--grace-seconds", "3", "--min-available-memory-mb", "0",
                     "--max-cpu-percent", "100", "--max-server-instances", "2",
                     "--max-total-instances", "4", "--", executable, "-I", "-c", echo],
                    cwd=directory, input=b"wheel-round-trip\n", capture_output=True,
                    timeout=45, creationflags=flags,
                )
                receipts = [json.loads(line) for line in completed.stderr.splitlines()]
                gone = any(row.get("event") == "postflight" and row.get("status") == "gone"
                           for row in receipts)
                released = state.exists() and json.loads(state.read_text(encoding="utf-8"))["leases"] == []
                if completed.returncode or completed.stdout != b"wheel-round-trip\n" or not gone or not released:
                    raise RuntimeError("installed Windows lifecycle incomplete")
                results["windows_owned_job"] = "passed"
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, RuntimeError):
            print(json.dumps({"ok": False, "checks": results, "reason": "installed-smoke-failed"}))
            return 1
    print(json.dumps({"ok": True, "checks": results, "scope": "installed-package-not-desktop"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
