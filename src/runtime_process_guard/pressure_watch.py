"""Low-cost resident pressure observation; no process termination authority."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import math
from pathlib import Path
import time

import psutil

from .lease_registry import LeaseRegistry, LeaseRegistryError


def decision(cpu: float, available_mb: float, process_count: int) -> str:
    if (not all(type(v) in (int, float) and math.isfinite(v) for v in (cpu, available_mb))
            or not 0 <= cpu <= 100 or available_mb < 0
            or type(process_count) is not int or process_count < 1):
        return "unknown"
    return "defer-new-work" if cpu >= 85 or available_mb < 2048 or process_count > 1100 else "continue"


def run(report: Path, stop_file: Path, *, interval: float = 10, max_cycles: int = 0) -> int:
    if (not report.is_absolute() or not stop_file.is_absolute() or report == stop_file
            or not math.isfinite(interval) or not 2 <= interval <= 300
            or type(max_cycles) is not int or max_cycles < 0):
        raise ValueError("invalid-watch-options")
    # Reuse the existing OS-held exclusive lock; crashes release it automatically.
    registry = LeaseRegistry(report, lock_timeout_seconds=0.1)
    cycle = failures = 0
    primed = False
    with registry._exclusive_lock():
        while not stop_file.exists():
            remaining = interval * min(6, 2 ** failures)
            while remaining > 0:
                if stop_file.exists():
                    return 0
                delay = min(1, remaining)
                time.sleep(delay)
                remaining -= delay
            if stop_file.exists():
                return 0
            cycle += 1
            started = time.monotonic()
            try:
                cpu = psutil.cpu_percent(interval=None)
                available = psutil.virtual_memory().available / 1048576
                count = len(psutil.pids())
                action = decision(cpu, available, count)
                if action == "unknown":
                    raise ValueError("invalid-observation")
                if not primed:
                    action = "warming-up"
                    primed = True
                payload = dict(cpu_percent=cpu, available_memory_mb=round(available, 1), process_count=count)
                failures = 0
            except (psutil.Error, OSError, ValueError):
                action, payload = "unknown", {}
                failures += 1
            result = {
                "schema": "runtime-process-guard/pressure-watch-v1",
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "action": action, "metrics": payload, "cycle": cycle,
                "consecutive_failures": failures, "read_only": True,
                "admission_enforced": False, "managed_launch_required": True,
                "collector_ms": round((time.monotonic() - started) * 1000, 2),
            }
            registry._assert_path_safe()
            from .cli import _write_report
            _write_report(report, result)
            if failures >= 3:
                return 40
            if max_cycles and cycle >= max_cycles:
                return 0
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-path", type=Path, required=True)
    parser.add_argument("--stop-file", type=Path, required=True)
    parser.add_argument("--interval-seconds", type=float, default=10)
    parser.add_argument("--max-cycles", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        return run(args.report_path, args.stop_file, interval=args.interval_seconds, max_cycles=args.max_cycles)
    except (ValueError, OSError, LeaseRegistryError):
        return 40


if __name__ == "__main__":
    raise SystemExit(main())
