"""Guarded stdio launcher for a bounded, opt-in MCP pilot."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import math
import os
import subprocess
import sys
import threading
import time
from typing import Sequence

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

    @property
    def enforcement_enabled(self) -> bool:
        return self.mode is GuardMode.ENFORCE


def validate_guard_options(
    *, mode: GuardMode, idle_seconds: float, grace_seconds: float, command: Sequence[str]
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
    if mode is GuardMode.ENFORCE:
        raise ValueError("enforce is unavailable until active JSON-RPC requests are tracked")
    return GuardOptions(mode, float(idle_seconds), float(grace_seconds), tuple(command))


def _receipt(event: str, **fields: object) -> None:
    # Never log argv, paths, message bodies, or environment values.
    print(json.dumps({"schema": "runtime-process-guard/stdio-v1", "event": event, **fields}, sort_keys=True), file=sys.stderr, flush=True)


def run_guarded_stdio(options: GuardOptions) -> int:
    """Relay stdio and manage only the process started by this invocation."""

    if os.name != "nt":
        raise OSError("guarded-stdio shadow pilot is currently Windows-only")
    job = WindowsJob()
    child: subprocess.Popen[bytes] | None = None
    try:
        child = subprocess.Popen(
            options.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            shell=False,
            bufsize=0,
            creationflags=0x00000004 if os.name == "nt" else 0,
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
        raise

    stdin_ended = threading.Event()
    relay_failed = threading.Event()

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
        try:
            while True:
                chunk = child.stdout.read(65536)
                if not chunk:
                    return
                sys.stdout.buffer.write(chunk)
                sys.stdout.buffer.flush()
        except (BrokenPipeError, OSError, ValueError):
            stdin_ended.set()
            relay_failed.set()

    input_thread = threading.Thread(target=relay_input, name="guard-stdin", daemon=True)
    output_thread = threading.Thread(target=relay_output, name="guard-stdout", daemon=True)
    input_thread.start()
    output_thread.start()
    _receipt("started", mode=options.mode.value, managed_job=job is not None)
    shutdown_started: float | None = None
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
            return 70
        return return_code
    finally:
        if job is not None:
            job.close()
