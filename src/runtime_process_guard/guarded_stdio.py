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
from .lease_registry import (
    LeaseCapacityError,
    LeaseRegistry,
    LeaseRegistryError,
    LeaseRegistryLockedError,
)
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
    max_server_instances: int | None = None
    max_total_instances: int | None = None
    client_owner_pid: int | None = None
    client_owner_created_at: datetime | None = None

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
    max_server_instances: int | None = None,
    max_total_instances: int | None = None,
    client_owner_pid: int | None = None,
    client_owner_created_at: datetime | None = None,
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
        raise ValueError(
            "enforce is unavailable until active JSON-RPC requests are tracked"
        )
    if (max_server_instances is None) != (max_total_instances is None):
        raise ValueError("instance limits must be supplied together")
    for limit in (max_server_instances, max_total_instances):
        if limit is not None and (type(limit) is not int or limit <= 0):
            raise ValueError("instance limits must be positive integers")
    if max_server_instances is not None and max_server_instances > max_total_instances:
        raise ValueError("server instance limit must not exceed the total limit")
    if (client_owner_pid is None) != (client_owner_created_at is None):
        raise ValueError("client owner PID and creation time must be supplied together")
    if client_owner_pid is not None:
        if type(client_owner_pid) is not int or client_owner_pid <= 0:
            raise ValueError("client owner PID must be a positive integer")
        if (
            not isinstance(client_owner_created_at, datetime)
            or client_owner_created_at.utcoffset() is None
        ):
            raise ValueError("client owner creation time must be timezone-aware")
    return GuardOptions(
        mode,
        float(idle_seconds),
        float(grace_seconds),
        tuple(command),
        Path(lease_state) if lease_state is not None else None,
        float(lease_ttl_seconds),
        min_available_memory_mb,
        float(max_cpu_percent),
        max_server_instances,
        max_total_instances,
        client_owner_pid,
        client_owner_created_at,
    )


def _receipt(event: str, **fields: object) -> bool:
    # Never log argv, paths, message bodies, or environment values.
    try:
        print(
            json.dumps(
                {"schema": "runtime-process-guard/stdio-v1", "event": event, **fields},
                sort_keys=True,
            ),
            file=sys.stderr,
            flush=True,
        )
    except (BrokenPipeError, OSError, ValueError):
        return False
    return True


def _relay_output(
    source: BinaryIO, destination: BinaryIO, relay_failed: threading.Event
) -> None:
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
    created_at = datetime.fromtimestamp(
        psutil.Process(os.getpid()).create_time(), timezone.utc
    )
    return LeaseRecord(
        lease_id=f"lease_{uuid4().hex}",
        owner_identity=_anonymous_identity(
            "owner", f"{os.getpid()}:{created_at.isoformat()}"
        ),
        server_identity=f"server_{command_identity(options.command)}",
        owner_pid=os.getpid(),
        owner_created_at=created_at,
        acquired_at=now,
        heartbeat_at=now,
        expires_at=now + timedelta(seconds=options.lease_ttl_seconds),
    )


def _settle_owned_job(job: WindowsJob, timeout_seconds: float) -> str:
    """Verify one owned Job, terminate remaining descendants, then verify again."""
    status = verify_owned_disappearance(
        job.active_process_count,
        timeout_seconds=timeout_seconds,
    )
    if status != "still-running":
        return status
    try:
        job.terminate()
    except (OSError, RuntimeError):
        return "unknown"
    _receipt("managed-job-terminated", reason="postflight-descendants")
    return verify_owned_disappearance(
        job.active_process_count,
        timeout_seconds=timeout_seconds,
    )


def _owned_job_alive(job: WindowsJob) -> bool | None:
    try:
        return job.active_process_count() > 0
    except (OSError, RuntimeError):
        return None


def _client_owner_status(pid: int, created_at: datetime) -> str:
    """Never infer an owner from its name or treat unreadability as death."""
    try:
        observed = datetime.fromtimestamp(
            psutil.Process(pid).create_time(), timezone.utc
        )
        return "alive" if observed == created_at else "ended"
    except psutil.NoSuchProcess:
        return "ended"
    except (psutil.AccessDenied, OSError, ValueError, OverflowError):
        return "unknown"


def _client_owner_shutdown_reason(status: str) -> str | None:
    """Fail closed when an explicitly tracked client owner cannot be trusted."""
    if status == "alive":
        return None
    if status == "ended":
        return "client-owner-ended"
    return "client-owner-unknown"


def _heartbeat_with_retry(
    registry: LeaseRegistry, lease: LeaseRecord, ttl: float
) -> LeaseRecord:
    remaining = (lease.expires_at - datetime.now(timezone.utc)).total_seconds()
    deadline = time.monotonic() + remaining
    for attempt in range(3):
        if time.monotonic() >= deadline:
            raise LeaseRegistryError("heartbeat renewal deadline exhausted")
        now = datetime.now(timezone.utc)
        try:
            return registry.heartbeat(
                lease.lease_id,
                lease.owner_identity,
                heartbeat_at=now,
                expires_at=now + timedelta(seconds=ttl),
                renewal_deadline=lease.expires_at,
            )
        except LeaseRegistryLockedError:
            if attempt == 2 or time.monotonic() + 0.05 >= deadline:
                raise
            time.sleep(0.05)


def run_guarded_stdio(options: GuardOptions) -> int:
    """Relay stdio and manage only the process started by this invocation."""

    if os.name != "nt":
        raise OSError("guarded-stdio shadow pilot is currently Windows-only")
    if options.lease_state is None:
        raise ValueError("guarded-stdio requires an explicit lease_state")
    if (
        options.client_owner_pid is not None
        and _client_owner_status(
            options.client_owner_pid, options.client_owner_created_at
        )
        != "alive"
    ):
        _receipt("client-owner-unknown", phase="preflight")
        return 40
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

    registry = LeaseRegistry(
        options.lease_state,
        lock_timeout_seconds=min(0.5, options.lease_ttl_seconds / 6),
    )
    lease = _make_lease(options)
    try:
        if options.max_server_instances is None:
            registry.acquire(lease)
        else:
            registry.acquire(
                lease,
                max_server_instances=options.max_server_instances,
                max_total_instances=options.max_total_instances,
            )
    except LeaseCapacityError:
        _receipt("admission", decision="defer", reasons=["lease-capacity"])
        return 20
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
        disappeared = child is None
        try:
            if child is not None and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=options.grace_seconds)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=options.grace_seconds)
        except BaseException:
            # Cleanup is best-effort here; preserve the original launch failure.
            _receipt("lease-state-unknown", phase="launch-cleanup-error")
        finally:
            if child is not None:
                disappeared = child.poll() is not None
            if job is not None:
                job.close()
            if disappeared:
                try:
                    registry.release(lease.lease_id, lease.owner_identity)
                except LeaseRegistryError:
                    _receipt("lease-state-unknown", phase="launch-cleanup")
            else:
                _receipt("lease-state-unknown", phase="launch-cleanup-disappearance")
        raise

    stdin_ended = threading.Event()
    relay_failed = threading.Event()
    heartbeat_failed = threading.Event()
    heartbeat_stop = threading.Event()

    def heartbeat() -> None:
        interval = max(0.1, options.lease_ttl_seconds / 3)
        current_lease = lease
        while not heartbeat_stop.wait(interval):
            try:
                current_lease = _heartbeat_with_retry(
                    registry, current_lease, options.lease_ttl_seconds
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
    output_thread = threading.Thread(
        target=relay_output, name="guard-stdout", daemon=True
    )
    input_thread.start()
    output_thread.start()
    heartbeat_thread = threading.Thread(
        target=heartbeat, name="guard-lease-heartbeat", daemon=True
    )
    heartbeat_thread.start()
    _receipt("started", mode=options.mode.value, managed_job=job is not None)
    shutdown_started: float | None = None
    lifetime_unknown = False
    client_owner_unknown = False
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
        postflight = _settle_owned_job(job, options.grace_seconds)
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
        while True:
            now = time.monotonic()
            # A shim may exit while its server still owns the inherited stdout.
            # The owned Job, not the directly spawned shim, defines lifetime.
            job_alive = _owned_job_alive(job)
            if job_alive is False:
                break
            should_close = stdin_ended.is_set() or relay_failed.is_set()
            reason = "relay-ended"
            if job_alive is None or heartbeat_failed.is_set():
                lifetime_unknown = True
                should_close = True
                reason = "owned-state-unknown"
            if options.client_owner_pid is not None:
                owner_status = _client_owner_status(
                    options.client_owner_pid, options.client_owner_created_at
                )
                owner_reason = _client_owner_shutdown_reason(owner_status)
                if owner_reason is not None:
                    should_close = True
                    reason = owner_reason
                if owner_status == "unknown":
                    if not client_owner_unknown:
                        _receipt("client-owner-unknown", phase="monitor")
                    client_owner_unknown = True
            if should_close and shutdown_started is None:
                shutdown_started = now
                if child.stdin is not None and not child.stdin.closed:
                    child.stdin.close()
                _receipt("graceful-shutdown-requested", reason=reason)
            if (
                shutdown_started is not None
                and now - shutdown_started >= options.grace_seconds
            ):
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
        if lifetime_unknown or client_owner_unknown:
            return 40
        return return_code
    except BaseException:
        if _owned_job_alive(job) is not False:
            if child.stdin is not None and not child.stdin.closed:
                child.stdin.close()
            _receipt("graceful-shutdown-requested", reason="guard-interrupted")
            # The direct child may already be gone; postflight still owns and
            # settles surviving descendants using the same grace period.
        finalize_lease()
        raise
    finally:
        heartbeat_stop.set()
        if not finalize_attempted:
            finalize_lease()
        if job is not None:
            job.close()
