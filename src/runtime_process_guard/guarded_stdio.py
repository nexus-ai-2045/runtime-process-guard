"""Guarded stdio launcher for a bounded, opt-in MCP pilot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
import math
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import BinaryIO, Sequence
from uuid import uuid4

import psutil

from .admission import Budget, evaluate
from .collector import observe
from .lease import LeaseRecord
from .lease_registry import LeaseRegistry, LeaseRegistryError
from .postflight import verify_owned_disappearance
from .privacy import command_identity
from .windows_job import WindowsJob


class GuardMode(str, Enum):
    SHADOW = "shadow"
    ENFORCE = "enforce"


@dataclass(frozen=True)
class GuardOptions:
    mode: GuardMode
    idle_seconds: float
    grace_seconds: float
    command: tuple[str, ...]
    lease_state: Path | None = None
    lease_ttl_seconds: float = 30.0
    min_available_memory_mb: int = 2048
    max_cpu_percent: float = 90.0

    @property
    def enforcement_enabled(self) -> bool:
        return self.mode is GuardMode.ENFORCE


def validate_guard_options(
    *,
    mode: GuardMode,
    idle_seconds: float,
    grace_seconds: float,
    command: Sequence[str],
    lease_state: Path | None = None,
    lease_ttl_seconds: float = 30.0,
    min_available_memory_mb: int = 2048,
    max_cpu_percent: float = 90.0,
) -> GuardOptions:
    if isinstance(command, (str, bytes)):
        raise TypeError("command must be an argument sequence, not shell text")
    if not command or not all(isinstance(item, str) and item for item in command):
        raise ValueError("command must contain non-empty arguments")
    if idle_seconds <= 0:
        raise ValueError("idle_seconds must be positive")
    if grace_seconds <= 0:
        raise ValueError("grace_seconds must be positive")
    if not math.isfinite(idle_seconds):
        raise ValueError("idle_seconds must be finite")
    if not math.isfinite(grace_seconds):
        raise ValueError("grace_seconds must be finite")
    if lease_ttl_seconds <= 0 or not math.isfinite(lease_ttl_seconds):
        raise ValueError("lease_ttl_seconds must be positive and finite")
    if min_available_memory_mb < 0:
        raise ValueError("min_available_memory_mb must be non-negative")
    if max_cpu_percent <= 0 or not math.isfinite(max_cpu_percent):
        raise ValueError("max_cpu_percent must be positive and finite")
    if mode is GuardMode.ENFORCE:
        raise ValueError("enforce is unavailable until active JSON-RPC requests are tracked")
    return GuardOptions(
        mode,
        float(idle_seconds),
        float(grace_seconds),
        tuple(command),
        Path(lease_state) if lease_state is not None else None,
        float(lease_ttl_seconds),
        min_available_memory_mb,
        float(max_cpu_percent),
    )


def _receipt(event: str, **fields: object) -> None:
    # Never log argv, paths, message bodies, or environment values.
    print(json.dumps({"schema": "runtime-process-guard/stdio-v1", "event": event, **fields}, sort_keys=True), file=sys.stderr, flush=True)


def _relay_output(source: BinaryIO, destination: BinaryIO, relay_failed: threading.Event) -> None:
    try:
        while True:
            chunk = source.read(65536)
            if not chunk:
                relay_failed.set()
                return
            destination.write(chunk)
            destination.flush()
    except (BrokenPipeError, OSError, ValueError):
        relay_failed.set()


def _anonymous_identity(prefix: str, value: str) -> str:
    return f"{prefix}_{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _make_lease(options: GuardOptions) -> LeaseRecord:
    now = datetime.now(timezone.utc)
    created_at = datetime.fromtimestamp(psutil.Process(os.getpid()).create_time(), timezone.utc)
    return LeaseRecord(
        lease_id=f"lease_{uuid4().hex}",
        owner_identity=_anonymous_identity("owner", f"{os.getpid()}:{created_at.isoformat()}"),
        server_identity=f"server_{command_identity(options.command)}",
        owner_pid=os.getpid(),
        owner_created_at=created_at,
        acquired_at=now,
        heartbeat_at=now,
        expires_at=now + timedelta(seconds=options.lease_ttl_seconds),
    )


def run_guarded_stdio(options: GuardOptions) -> int:
    """Relay stdio and manage only the process started by this invocation."""

    if os.name != "nt":
        raise OSError("guarded-stdio shadow pilot is currently Windows-only")
    if options.lease_state is None:
        raise ValueError("guarded-stdio requires an explicit lease_state")
    admission = evaluate(
        observe(options.command, reuse_policy="dedicated-stdio"),
        Budget(
            min_available_memory_mb=options.min_available_memory_mb,
            max_cpu_percent=options.max_cpu_percent,
        ),
    )
    _receipt("admission", decision=admission.decision, reasons=admission.reason_codes)
    if admission.decision != "allow":
        return {"reuse": 10, "defer": 20, "deny": 30, "unknown": 40}.get(
            admission.decision, 40
        )

    registry = LeaseRegistry(options.lease_state)
    lease = _make_lease(options)
    try:
        registry.acquire(lease)
    except LeaseRegistryError:
        _receipt("lease-state-unknown", phase="acquire")
        return 40

    job: WindowsJob | None = None
    child: subprocess.Popen[bytes] | None = None
    try:
        job = WindowsJob()
        child = subprocess.Popen(
            options.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
            bufsize=0,
            creationflags=0x08000004,
        )
        if job is not None:
            job.assign(child.pid)
            job.resume(child.pid)
    except BaseException:
        try:
            if child is not None and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=options.grace_seconds)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=options.grace_seconds)
        finally:
            if job is not None:
                job.close()
            try:
                registry.release(lease.lease_id, lease.owner_identity)
            except LeaseRegistryError:
                _receipt("lease-state-unknown", phase="launch-cleanup")
        raise

    stdin_ended = threading.Event()
    relay_failed = threading.Event()
    heartbeat_failed = threading.Event()
    heartbeat_stop = threading.Event()

    def heartbeat() -> None:
        interval = max(0.1, options.lease_ttl_seconds / 3)
        while not heartbeat_stop.wait(interval):
            now = datetime.now(timezone.utc)
            try:
                registry.heartbeat(
                    lease.lease_id,
                    lease.owner_identity,
                    heartbeat_at=now,
                    expires_at=now + timedelta(seconds=options.lease_ttl_seconds),
                )
            except LeaseRegistryError:
                heartbeat_failed.set()
                _receipt("lease-state-unknown", phase="heartbeat")
                return

    def relay_input() -> None:
        assert child.stdin is not None
        try:
            while True:
                # read1 returns currently available pipe data instead of waiting to
                # fill the whole buffer, which is required for persistent MCP frames.
                chunk = sys.stdin.buffer.read1(65536)
                if not chunk:
                    stdin_ended.set()
                    child.stdin.close()
                    return
                child.stdin.write(chunk)
                child.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            stdin_ended.set()
            relay_failed.set()

    def relay_output() -> None:
        assert child.stdout is not None
        _relay_output(child.stdout, sys.stdout.buffer, relay_failed)

    input_thread = threading.Thread(target=relay_input, name="guard-stdin", daemon=True)
    output_thread = threading.Thread(target=relay_output, name="guard-stdout", daemon=True)
    input_thread.start()
    output_thread.start()
    heartbeat_thread = threading.Thread(
        target=heartbeat, name="guard-lease-heartbeat", daemon=True
    )
    heartbeat_thread.start()
    _receipt("started", mode=options.mode.value, managed_job=job is not None)
    shutdown_started: float | None = None
    finalize_attempted = False

    def finalize_lease() -> bool:
        nonlocal finalize_attempted
        finalize_attempted = True
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=options.grace_seconds)
        if heartbeat_thread.is_alive():
            _receipt("lease-state-unknown", phase="heartbeat-stop")
            return False
        if job is None:
            return False
        postflight = verify_owned_disappearance(
            job.active_process_count,
            timeout_seconds=options.grace_seconds,
        )
        _receipt("postflight", status=postflight)
        if postflight != "gone" or heartbeat_failed.is_set():
            return False
        try:
            registry.release(lease.lease_id, lease.owner_identity)
        except LeaseRegistryError:
            _receipt("lease-state-unknown", phase="release")
            return False
        return True

    try:
        while child.poll() is None:
            now = time.monotonic()
            should_close = stdin_ended.is_set() or relay_failed.is_set()
            if should_close and shutdown_started is None:
                shutdown_started = now
                if child.stdin is not None and not child.stdin.closed:
                    child.stdin.close()
                _receipt("graceful-shutdown-requested", reason="relay-ended")
            if shutdown_started is not None and now - shutdown_started >= options.grace_seconds:
                if job is not None:
                    job.terminate()
                    _receipt("managed-job-terminated", reason="grace-timeout")
                else:
                    child.terminate()
                    _receipt("managed-process-terminated", reason="grace-timeout")
                break
            time.sleep(0.05)
        return_code = child.wait(timeout=options.grace_seconds)
        output_thread.join(timeout=options.grace_seconds)
        if output_thread.is_alive():
            _receipt("stdout-drain-timeout")
            return_code = 70
        if not finalize_lease():
            return 40
        return return_code
    except BaseException:
        if child.poll() is None:
            if child.stdin is not None and not child.stdin.closed:
                child.stdin.close()
            _receipt("graceful-shutdown-requested", reason="guard-interrupted")
            try:
                child.wait(timeout=options.grace_seconds)
            except subprocess.TimeoutExpired:
                job.terminate()
                _receipt("managed-job-terminated", reason="guard-interrupted-timeout")
                child.wait(timeout=options.grace_seconds)
        finalize_lease()
        raise
    finally:
        heartbeat_stop.set()
        if not finalize_attempted:
            finalize_lease()
        if job is not None:
            job.close()
