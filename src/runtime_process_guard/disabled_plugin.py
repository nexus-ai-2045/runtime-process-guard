from __future__ import annotations

import json
import hashlib
import math
import os
import re
import stat
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

import psutil

from .postflight import verify_owned_disappearance
from .privacy import command_identity, safe_executable_name


class ReconcileError(Exception):
    pass


@dataclass(frozen=True)
class Inputs:
    config: Path
    plugin_root: Path
    plugin_id: str
    server_name: str
    expected_executable: Path
    owner_pid: int
    owner_created: float
    max_targets: int


def _safe_path(path: Path) -> Path:
    if not path.is_absolute() or ".." in path.parts:
        raise ReconcileError("unsafe-source")
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ReconcileError("unsafe-source")
    return path.resolve(strict=True)


def _file_revision(path: Path) -> dict:
    data = path.stat()
    return {"device": data.st_dev, "inode": data.st_ino,
            "size": data.st_size, "modified_ns": data.st_mtime_ns,
            "changed_ns": data.st_ctime_ns}


def _read_source(path: Path) -> tuple[bytes, dict]:
    path = _safe_path(path)
    before = _file_revision(path)
    mode = path.stat()
    if (not stat.S_ISREG(mode.st_mode) or mode.st_mode & 0o022
            or mode.st_size > 1024 * 1024):
        raise ReconcileError("unsafe-source")
    raw = path.read_bytes()
    if before != _file_revision(path):
        raise ReconcileError("source-changed")
    return raw, before


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate-key")
        result[key] = value
    return result


def _server_binding(root: Path, definition: dict) -> tuple[Path, list[str]] | None:
    if not isinstance(definition, dict):
        raise ReconcileError("invalid-server")
    if "command" not in definition and isinstance(definition.get("url"), str):
        return None
    command = definition.get("command")
    args = definition.get("args", [])
    cwd_value = definition.get("cwd", ".")
    if (not isinstance(command, str) or not command
            or not isinstance(args, list) or not all(isinstance(a, str) for a in args)
            or not isinstance(cwd_value, str)):
        raise ReconcileError("invalid-server")
    cwd = root / cwd_value
    if ".." in cwd.parts or not _safe_path(cwd).is_relative_to(root):
        raise ReconcileError("invalid-server")
    return _safe_path(cwd), [command, *args]


def _context(inputs: Inputs) -> tuple[dict, Path, list[str]]:
    if os.name != "posix":
        raise ReconcileError("unsupported-platform")
    if (not re.fullmatch(r"[A-Za-z0-9._-]+@[A-Za-z0-9._-]+", inputs.plugin_id)
            or not re.fullmatch(r"[A-Za-z0-9._-]+", inputs.server_name)
            or type(inputs.max_targets) is not int
            or not 1 <= inputs.max_targets <= 64
            or inputs.owner_pid <= 1
            or not math.isfinite(inputs.owner_created)):
        raise ReconcileError("invalid-input")
    root = _safe_path(inputs.plugin_root)
    name, market = inputs.plugin_id.split("@")
    if root.parent.name != name or root.parent.parent.name != market:
        raise ReconcileError("plugin-binding-mismatch")
    revisions = {}
    raw, revisions["settings"] = _read_source(inputs.config)
    try:
        config = tomllib.loads(raw.decode())
        plugin = config.get("plugins", {}).get(inputs.plugin_id, {})
        server = plugin.get("mcp_servers", {}).get(inputs.server_name, {})
        disabled = plugin.get("enabled") is False or server.get("enabled") is False
    except (ValueError, TypeError, AttributeError, UnicodeError):
        raise ReconcileError("invalid-settings") from None
    if not disabled:
        raise ReconcileError("target-not-disabled")
    raw, revisions["metadata"] = _read_source(root / ".codex-plugin/plugin.json")
    try:
        metadata = json.loads(raw, object_pairs_hook=_unique_object)
        if (metadata.get("name") != name
                or metadata.get("mcpServers") not in {".mcp.json", "./.mcp.json"}):
            raise ValueError
        raw, revisions["manifest"] = _read_source(root / ".mcp.json")
        manifest = json.loads(raw, object_pairs_hook=_unique_object)
        definition = manifest["mcpServers"][inputs.server_name]
        if not isinstance(definition, dict):
            raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        raise ReconcileError("invalid-manifest") from None
    binding = _server_binding(root, definition)
    if binding is None:
        raise ReconcileError("invalid-server")
    cwd, expected = binding
    for key, sibling in manifest["mcpServers"].items():
        if key == inputs.server_name:
            continue
        other = _server_binding(root, sibling)
        if (other is not None and other[0] == cwd and other[1][1:] == expected[1:]
                and safe_executable_name(other[1][0]) == safe_executable_name(expected[0])):
            raise ReconcileError("ambiguous-server-binding")
    expected_exe = _safe_path(inputs.expected_executable)
    if not expected_exe.is_file():
        raise ReconcileError("invalid-executable")
    declared = Path(expected[0])
    if "/" in expected[0]:
        if _safe_path(declared if declared.is_absolute() else cwd / declared) != expected_exe:
            raise ReconcileError("unsupported-command-binding")
    elif expected[0] != safe_executable_name(str(expected_exe)):
        raise ReconcileError("unsupported-command-binding")
    owner = psutil.Process(inputs.owner_pid)
    if (owner.create_time() != inputs.owner_created
            or owner.uids().real != os.getuid()
            or owner.status() == psutil.STATUS_ZOMBIE):
        raise ReconcileError("owner-changed")
    owner_exe = _safe_path(Path(owner.exe()))
    context = {"sources": revisions,
               "owner": {"pid": owner.pid, "created": owner.create_time(),
                         "uid": owner.uids().real,
                         "executable_revision": _file_revision(owner_exe)},
               "cwd_inode": {"device": cwd.stat().st_dev, "inode": cwd.stat().st_ino},
               "expected_executable_revision": _file_revision(expected_exe),
               "command_identity": command_identity(expected)}
    return context, cwd, expected


def _candidate(process, inputs: Inputs, cwd: Path, expected: list[str]) -> dict | None:
    if process.status() == psutil.STATUS_ZOMBIE:
        return None
    argv = process.cmdline()
    if argv != expected or Path(process.cwd()) != cwd:
        return None
    if process.ppid() != inputs.owner_pid or process.uids().real != os.getuid():
        raise ReconcileError("candidate-owner-mismatch")
    exe = _safe_path(Path(process.exe()))
    if exe != _safe_path(inputs.expected_executable):
        raise ReconcileError("candidate-executable-mismatch")
    return {"pid": process.pid, "created": process.create_time(),
            "parent_pid": process.ppid(), "uid": process.uids().real,
            "executable": safe_executable_name(str(exe)),
            "executable_revision": _file_revision(exe),
            "command_identity": command_identity(argv)}


def collect_plan(inputs: Inputs) -> dict:
    context, cwd, expected = _context(inputs)
    candidates = []
    for process in psutil.Process(inputs.owner_pid).children():
        try:
            row = _candidate(process, inputs, cwd, expected)
            if row:
                candidates.append(row)
        except psutil.NoSuchProcess:
            continue
    after, _, _ = _context(inputs)
    if after != context:
        raise ReconcileError("source-changed")
    if len(candidates) > inputs.max_targets:
        raise ReconcileError("target-limit-exceeded")
    now = time.time()
    return {"schema": "runtime-process-guard/disabled-plugin-plan-v1",
            "configuration_scope": "operator-selected-saved-settings",
            "idle_evidence": "operator-confirmation-required",
            "binding": context, "targets": sorted(candidates, key=lambda p: p["pid"]),
            "max_targets": inputs.max_targets, "created_at": now, "expires_at": now + 300}


def _stable(plan: dict) -> dict:
    return {key: value for key, value in plan.items()
            if key not in {"created_at", "expires_at"}}


def _terminate(row: dict):
    process = psutil.Process(row["pid"])
    if process.create_time() != row["created"]:
        raise ReconcileError("candidate-generation-changed")
    process.terminate()


def _remaining(rows: list[dict]) -> int:
    count = 0
    for row in rows:
        try:
            process = psutil.Process(row["pid"])
            if process.create_time() == row["created"] and process.status() != psutil.STATUS_ZOMBIE:
                count += 1
        except psutil.NoSuchProcess:
            continue
        except psutil.Error:
            raise RuntimeError("observation-unavailable") from None
    return count


def apply_plan(inputs: Inputs, plan: dict, *, idle_confirmed: bool,
               wait_seconds: float = 5) -> dict:
    if not idle_confirmed:
        raise ReconcileError("operator-confirmation-required")
    if (not math.isfinite(wait_seconds) or not 0 <= wait_seconds <= 30
            or not isinstance(plan, dict)):
        raise ReconcileError("invalid-plan")
    try:
        if (not plan["created_at"] <= time.time() <= plan["expires_at"]
                or not 0 < plan["expires_at"] - plan["created_at"] <= 300):
            raise ReconcileError("plan-expired")
        fresh = collect_plan(inputs)
        if _stable(fresh) != _stable(plan):
            raise ReconcileError("plan-changed")
    except (KeyError, TypeError):
        raise ReconcileError("invalid-plan") from None
    stopped = []
    failure = None
    for row in plan["targets"]:
        try:
            current = collect_plan(inputs)
            if (current["binding"] != plan["binding"]
                    or any(p not in plan["targets"] for p in current["targets"])
                    or row not in current["targets"]):
                raise ReconcileError("plan-changed-before-signal")
            _terminate(row)
            stopped.append(row)
        except (ReconcileError, psutil.Error, OSError):
            failure = "signal-or-revalidation-failed"
            break
    status = verify_owned_disappearance(
        lambda: _remaining(plan["targets"]), timeout_seconds=wait_seconds)
    try:
        matching = len(collect_plan(inputs)["targets"])
    except (ReconcileError, psutil.Error, OSError):
        matching = None
    overall = ("partial-stop" if failure and stopped else "unknown" if failure else
               "stopped" if status == "gone" and matching == 0 else "unknown")
    return {"schema": "runtime-process-guard/disabled-plugin-stop-v1",
            "overall": overall, "signal": "SIGTERM", "requested_count": len(plan["targets"]),
            "signal_sent_count": len(stopped), "signal_sent_pids": [r["pid"] for r in stopped],
            "unexecuted_pids": [r["pid"] for r in plan["targets"] if r not in stopped],
            "selected_generations_gone": status == "gone", "postflight": status,
            "remaining_matching_processes": matching, "failure": failure,
            "idle_confirmation": "operator", "descendants_managed": False}


def run_command(args) -> tuple[dict, int]:
    inputs = Inputs(args.config, args.plugin_root, args.plugin_id, args.server_name,
                    args.expected_executable,
                    args.owner_pid, args.owner_created_at.timestamp(), args.max_targets)
    try:
        if args.apply:
            if (not isinstance(args.expected_plan_sha256, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", args.expected_plan_sha256)):
                raise ReconcileError("plan-digest-required")
            raw, _ = _read_source(args.plan_path)
            if hashlib.sha256(raw).hexdigest() != args.expected_plan_sha256:
                raise ReconcileError("plan-digest-mismatch")
            plan = json.loads(raw, object_pairs_hook=_unique_object)
            result = apply_plan(inputs, plan, idle_confirmed=args.idle_confirmed,
                                wait_seconds=args.wait_seconds)
            return result, 0 if result["overall"] == "stopped" else 40
        plan = collect_plan(inputs)
        payload = (json.dumps(plan, sort_keys=True) + "\n").encode()
        path = args.plan_path
        _safe_path(path.parent)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        return {"overall": "planned", "plan": plan,
                "plan_sha256": hashlib.sha256(payload).hexdigest(),
                "processes_stopped": 0}, 0
    except ReconcileError as exc:
        return {"overall": "unknown", "reason": str(exc), "processes_stopped": 0}, 40
    except (OSError, ValueError, TypeError, psutil.Error):
        return {"overall": "unknown", "reason": "observation-or-storage-unavailable",
                "processes_stopped": 0}, 40
