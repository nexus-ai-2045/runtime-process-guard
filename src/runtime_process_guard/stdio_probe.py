"""Read-only observations of inherited stdio; observations never prove ownership."""

from __future__ import annotations

import ctypes
from datetime import datetime, timezone
import json
import math
import os
import sys
from typing import Callable

import psutil

SCHEMA = "runtime-process-guard/stdio-probe-v1"
FRAME_LIMIT = 65536
PROTOCOL_VERSIONS = {"2024-11-05", "2025-03-26", "2025-06-18"}


def _identity(pid: int) -> dict:
    try:
        created = psutil.Process(pid).create_time()
        if not math.isfinite(created):
            raise ValueError
        return {
            "status": "observed",
            "pid": pid,
            "created_at": datetime.fromtimestamp(created, timezone.utc).isoformat(),
        }
    except (psutil.Error, OSError, ValueError, OverflowError):
        return {"status": "unknown", "pid": pid}


class WindowsStdioQueries:
    """Queries only the handles already inherited by this process."""

    def __init__(self) -> None:
        from ctypes import wintypes

        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.GetStdHandle.argtypes = [wintypes.DWORD]
        self.kernel.GetStdHandle.restype = wintypes.HANDLE
        self.kernel.GetFileType.argtypes = [wintypes.HANDLE]
        self.kernel.GetFileType.restype = wintypes.DWORD
        self.kernel.GetNamedPipeInfo.argtypes = [wintypes.HANDLE] + [
            ctypes.POINTER(wintypes.DWORD)
        ] * 4
        self.kernel.GetNamedPipeInfo.restype = wintypes.BOOL
        for name in ("GetNamedPipeServerProcessId", "GetNamedPipeClientProcessId"):
            function = getattr(self.kernel, name)
            function.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            function.restype = wintypes.BOOL
        self.dword = wintypes.DWORD

    def __call__(self, stream: str) -> dict:
        ctypes.set_last_error(0)
        handle = self.kernel.GetStdHandle(self.dword(-10 if stream == "stdin" else -11))
        if handle in (None, 0, ctypes.c_void_p(-1).value):
            return {"status": "unknown", "handle_error": ctypes.get_last_error()}
        ctypes.set_last_error(0)
        file_type = int(self.kernel.GetFileType(handle))
        type_error = ctypes.get_last_error()
        result = {
            "status": "observed" if file_type else "unknown",
            "file_type": file_type,
            "file_type_error": type_error,
        }
        if file_type != 3:
            return result
        flags = self.dword()
        ctypes.set_last_error(0)
        success = bool(
            self.kernel.GetNamedPipeInfo(handle, ctypes.byref(flags), None, None, None)
        )
        result["pipe_info"] = {
            "success": success,
            "error": 0 if success else ctypes.get_last_error(),
        }
        for label, name in (
            ("server", "GetNamedPipeServerProcessId"),
            ("client", "GetNamedPipeClientProcessId"),
        ):
            pid = self.dword()
            ctypes.set_last_error(0)
            success = bool(getattr(self.kernel, name)(handle, ctypes.byref(pid)))
            result[label] = {
                "success": success,
                "error": 0 if success else ctypes.get_last_error(),
            }
            if success:
                result[label]["pid"] = int(pid.value)
        return result


def _metadata(observation: dict) -> dict:
    """Export an allowlist, never caller-supplied transport bytes or strings."""
    result = {
        "status": "observed" if observation.get("status") == "observed" else "unknown"
    }
    for key in ("handle_error", "file_type", "file_type_error"):
        if type(observation.get(key)) is int and observation[key] >= 0:
            result[key] = observation[key]
    for key in ("pipe_info", "server", "client"):
        query = observation.get(key)
        if isinstance(query, dict):
            result[key] = {"success": query.get("success") is True}
            if type(query.get("error")) is int and query["error"] >= 0:
                result[key]["error"] = query["error"]
            if (
                key != "pipe_info"
                and query.get("success") is True
                and type(query.get("pid")) is int
                and query["pid"] > 0
            ):
                result[key]["pid"] = query["pid"]
    return result


def build_stdio_report(
    observations: dict, *, self_pid: int, identity: Callable[[int], dict] = _identity
) -> dict:
    """Build a report from injected observations without granting owner authority."""
    observations = {
        stream: _metadata(observations.get(stream, {}))
        for stream in ("stdin", "stdout")
    }
    candidates = []
    query_complete = True
    for stream in ("stdin", "stdout"):
        observation = observations.get(stream, {})
        peers = set()
        complete = (
            observation.get("status") == "observed"
            and observation.get("file_type") == 3
            and observation.get("pipe_info", {}).get("success") is True
        )
        for label in ("server", "client"):
            query = observation.get(label, {})
            if query.get("success") is not True:
                complete = False
            pid = query.get("pid")
            if (
                query.get("success") is True
                and type(pid) is int
                and pid > 0
                and pid != self_pid
            ):
                peers.add(pid)
        query_complete = query_complete and complete
        candidates.append(peers)
    shared = candidates[0] & candidates[1]
    consistent = query_complete and len(shared) == 1 and candidates[0] == candidates[1]
    return {
        "schema": SCHEMA,
        "probe_status": "collected",
        "ownership_status": "unknown",
        "candidate_status": "consistent" if consistent else "unknown",
        "self": identity(self_pid),
        "streams": observations,
        "peer_candidates": [
            identity(pid) for pid in sorted(candidates[0] | candidates[1])
        ],
        "authority_granted": False,
    }


def collect_stdio_probe(
    *,
    query: Callable[[str], dict] | None = None,
    platform: str | None = None,
    identity: Callable[[int], dict] = _identity,
) -> dict:
    platform = sys.platform if platform is None else platform
    if platform != "win32":
        return {
            "schema": SCHEMA,
            "probe_status": "unsupported",
            "ownership_status": "unknown",
            "candidate_status": "unknown",
            "authority_granted": False,
        }
    try:
        query = WindowsStdioQueries() if query is None else query
        observations = {stream: query(stream) for stream in ("stdin", "stdout")}
        return build_stdio_report(observations, self_pid=os.getpid(), identity=identity)
    except (OSError, AttributeError, ValueError, TypeError):
        return {
            "schema": SCHEMA,
            "probe_status": "unknown",
            "ownership_status": "unknown",
            "candidate_status": "unknown",
            "authority_granted": False,
        }


def run_probe_mcp(
    *,
    collector: Callable[[], dict] = collect_stdio_probe,
    stdin=None,
    stdout=None,
    stderr=None,
) -> int:
    """Serve one bounded, read-only tool over newline-delimited JSON-RPC."""
    stdin = sys.stdin.buffer if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    initialized = False

    def send(payload: dict) -> None:
        stdout.write(json.dumps(payload, ensure_ascii=True) + "\n")
        stdout.flush()

    def error(request_id, code: int, message: str) -> None:
        send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": code, "message": message},
            }
        )

    while True:
        frame = stdin.readline(FRAME_LIMIT + 1)
        if not frame:
            stderr.write(
                json.dumps({"event": "stdio_probe_eof", "ownership_status": "unknown"})
                + "\n"
            )
            stderr.flush()
            return 0
        try:
            if len(frame) > FRAME_LIMIT:
                raise ValueError
            request = json.loads(frame)
            if (
                not isinstance(request, dict)
                or request.get("jsonrpc") != "2.0"
                or not isinstance(request.get("method"), str)
            ):
                raise ValueError
            request_id = request.get("id")
            if "id" in request and not (
                type(request_id) is int
                or isinstance(request_id, str)
                and len(request_id) <= 256
            ):
                raise ValueError
            params = request.get("params", {})
            if not isinstance(params, dict):
                raise ValueError
        except (ValueError, UnicodeError, TypeError):
            error(None, -32600, "Invalid request")
            return 40
        method = request["method"]
        if "id" not in request:
            # Valid notifications have no response. This diagnostic server has
            # no asynchronous work to cancel and ignores unknown notifications.
            continue
        if method == "initialize":
            version = params.get("protocolVersion")
            if (
                not isinstance(version, str)
                or not version
                or len(version) > 256
                or initialized
            ):
                error(request_id, -32602, "Unsupported initialization")
                continue
            if version not in PROTOCOL_VERSIONS:
                version = "2025-06-18"
            initialized = True
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": version,
                        "capabilities": {"tools": {}},
                        "serverInfo": {
                            "name": "runtime-process-guard-stdio-probe",
                            "version": "0.1.0",
                        },
                    },
                }
            )
        elif not initialized:
            error(request_id, -32600, "Initialization required")
        elif method == "ping":
            send({"jsonrpc": "2.0", "id": request_id, "result": {}})
        elif method == "tools/list":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "tools": [
                            {
                                "name": "stdio_owner_observation",
                                "description": "Read-only inherited stdio metadata; ownership remains unknown.",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {},
                                    "additionalProperties": False,
                                },
                                "annotations": {
                                    "readOnlyHint": True,
                                    "destructiveHint": False,
                                    "openWorldHint": False,
                                },
                            }
                        ]
                    },
                }
            )
        elif method == "tools/call":
            if (
                params.get("name") != "stdio_owner_observation"
                or params.get("arguments", {}) != {}
            ):
                error(request_id, -32602, "Invalid tool arguments")
                continue
            try:
                report = collector()
            except Exception:
                error(request_id, -32603, "Observation unavailable")
                continue
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(report, ensure_ascii=True),
                            }
                        ]
                    },
                }
            )
        else:
            error(request_id, -32601, "Method not found")
