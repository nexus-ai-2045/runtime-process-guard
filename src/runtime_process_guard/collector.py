"""Cross-platform, read-only process and resource collection."""

from __future__ import annotations

import os
from collections.abc import Sequence

import psutil

from .admission import Observation
from .privacy import command_identity, safe_executable_name


def observe(command: Sequence[str], *, cpu_sample_seconds: float = 0.2) -> Observation:
    identity = command_identity(command)
    duplicate_pids: list[int] = []
    inaccessible = 0
    errors: list[str] = []

    try:
        for process in psutil.process_iter(["pid", "cmdline"]):
            if process.pid == os.getpid():
                continue
            try:
                cmdline = process.info.get("cmdline") or []
                if cmdline and command_identity(cmdline) == identity:
                    duplicate_pids.append(process.pid)
            except (psutil.AccessDenied, psutil.ZombieProcess):
                inaccessible += 1
            except (ValueError, TypeError):
                continue
    except (OSError, psutil.Error):
        errors.append("process-enumeration-failed")

    try:
        memory_mb = int(psutil.virtual_memory().available / (1024 * 1024))
        cpu_percent = float(psutil.cpu_percent(interval=cpu_sample_seconds))
    except (OSError, psutil.Error):
        memory_mb = 0
        cpu_percent = 0.0
        errors.append("resource-collection-failed")

    return Observation(
        identity=identity,
        executable=safe_executable_name(command[0]),
        duplicate_pids=tuple(sorted(duplicate_pids)),
        available_memory_mb=memory_mb,
        cpu_percent=cpu_percent,
        collection_errors=tuple(errors),
        inaccessible_processes=inaccessible,
    )
