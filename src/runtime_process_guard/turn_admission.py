"""Two-chat FIFO admission through synchronous Codex hooks and completion notify."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from uuid import uuid4

import psutil

from .lease_registry import LeaseRegistry, LeaseRegistryError

SCHEMA = "runtime-process-guard/turn-admission-v1"
MAX_ROWS = 512


def key(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ValueError("invalid-turn-identity")
    return hashlib.sha256(value.encode()).hexdigest()


def alive(owner: list) -> bool:
    if (not isinstance(owner, list) or len(owner) != 2 or type(owner[0]) is not int
            or owner[0] <= 0 or type(owner[1]) not in (int, float)
            or not math.isfinite(owner[1]) or owner[1] <= 0):
        raise ValueError("invalid-process-generation")
    try:
        return psutil.Process(owner[0]).create_time() == owner[1]
    except psutil.NoSuchProcess:
        return False


def runtime_owner() -> list:
    for process in psutil.Process().parents():
        if process.name().lower() in ("codex.exe", "codex"):
            return [process.pid, process.create_time()]
    raise ValueError("codex-runtime-owner-unavailable")


class Gate:
    def __init__(self, path: Path):
        if not path.is_absolute():
            raise ValueError("absolute-state-required")
        self.path = path
        self.lock = LeaseRegistry(path, lock_timeout_seconds=0.5)

    def _read(self) -> dict:
        if not self.path.exists():
            return {"schema": SCHEMA, "limit": 2, "next_ticket": 0, "rows": [], "children": {}, "applied_releases": []}
        if self.path.stat().st_size > 1048576:
            raise ValueError("oversize-admission-state")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or data.get("schema") != SCHEMA or data.get("limit") != 2
                or type(data.get("next_ticket")) is not int or data["next_ticket"] < 0
                or not isinstance(data.get("rows"), list) or len(data["rows"]) > MAX_ROWS
                or not isinstance(data.get("children", {}), dict) or len(data.get("children", {})) > MAX_ROWS):
            raise ValueError("invalid-admission-state")
        data.setdefault("children", {})
        applied = data.setdefault("applied_releases", [])
        if not isinstance(applied, list) or any(not isinstance(x, str) or not re.fullmatch(r"[0-9a-f]{32}", x) for x in applied):
            raise ValueError("invalid-release-acknowledgements")
        if any(not re.fullmatch(r"[0-9a-f]{64}", x) for pair in data["children"].items() for x in pair):
            raise ValueError("invalid-child-binding")
        seen, tickets = set(), set()
        for row in data["rows"]:
            if not isinstance(row, dict):
                raise ValueError("invalid-admission-row")
            pair = (row["session"], row["turn"])
            if (any(not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v) for v in pair)
                    or pair in seen or row["state"] not in ("waiting", "active")
                    or type(row["ticket"]) is not int or row["ticket"] < 0
                    or row["ticket"] in tickets or row["ticket"] >= data["next_ticket"]):
                raise ValueError("invalid-admission-row")
            seen.add(pair)
            tickets.add(row["ticket"])
        if len({r["session"] for r in data["rows"] if r["state"] == "active"}) > 2:
            raise ValueError("capacity-invariant-broken")
        return data

    def _write(self, data: dict) -> None:
        from .cli import _write_report
        self.lock._assert_path_safe()
        _write_report(self.path, data)

    def _release_files(self, data: dict) -> list[Path]:
        directory = self.path.with_name(self.path.name + ".releases")
        LeaseRegistry(directory / "sentinel")._assert_path_safe()
        files = []
        present = set()
        if directory.exists():
            for marker in directory.glob("*.json"):
                if len(files) >= MAX_ROWS:
                    # Commit and clean a bounded batch before refusing admission.
                    # A recovered filesystem can therefore drain a backlog on
                    # subsequent calls instead of remaining permanently wedged.
                    self._write(data)
                    self._ack(files)
                    raise ValueError("release-backlog-retry")
                LeaseRegistry(marker)._assert_path_safe()
                if marker.stat().st_size > 1024:
                    raise ValueError("oversize-release-event")
                item = json.loads(marker.read_text(encoding="utf-8"))
                if (not isinstance(item, dict) or set(item) != {"session", "turn", "id"}
                        or not isinstance(item["id"], str) or not re.fullmatch(r"[0-9a-f]{32}", item["id"])
                        or not isinstance(item["session"], str)
                        or not re.fullmatch(r"[0-9a-f]{64}", item["session"])
                        or (item["turn"] is not None and (not isinstance(item["turn"], str)
                            or not re.fullmatch(r"[0-9a-f]{64}", item["turn"])))):
                    raise ValueError("invalid-release-event")
                present.add(item["id"])
                if item["id"] not in data["applied_releases"]:
                    data["rows"] = [r for r in data["rows"] if not (
                        r["session"] == item["session"] and (item["turn"] is None or r["turn"] == item["turn"]))]
                    data["applied_releases"].append(item["id"])
                files.append(marker)
        data["applied_releases"] = [x for x in data["applied_releases"] if x in present]
        active = {r["session"] for r in data["rows"] if r["state"] == "active"}
        data["children"] = {child: parent for child, parent in data["children"].items() if parent in active}
        return files

    @staticmethod
    def _ack(files: list[Path]) -> None:
        for path in files:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass  # committed acknowledgement makes later replay harmless

    def try_admit(self, session: str, turn: str, backend: list, waiter: list) -> bool:
        with self.lock._exclusive_lock():
            data = self._read()
            releases = self._release_files(data)
            # Only proven dead process generations are reclaimed. No age/idle guesses.
            data["rows"] = [r for r in data["rows"] if alive(r["backend"])
                            and (r["state"] == "active" or alive(r["waiter"]))]
            row = next((r for r in data["rows"] if (r["session"], r["turn"]) == (session, turn)), None)
            if row is None:
                if len(data["rows"]) >= MAX_ROWS or not alive(backend) or not alive(waiter):
                    raise ValueError("admission-unavailable")
                row = dict(session=session, turn=turn, backend=backend, waiter=waiter,
                           ticket=data["next_ticket"], state="waiting")
                data["next_ticket"] += 1
                data["rows"].append(row)
            active = {r["session"] for r in data["rows"] if r["state"] == "active"}
            waiting = [r for r in data["rows"] if r["state"] == "waiting"]
            if row["state"] == "active" or session in active or (
                len(active) < 2 and row is min(waiting, key=lambda r: r["ticket"])
            ):
                row["state"] = "active"
            self._write(data)
            self._ack(releases)
            return row["state"] == "active"

    def release(self, session: str, turn: str | None = None, *, waiting_only: bool = False) -> None:
        if not waiting_only:
            from .cli import _write_report
            identifier = uuid4().hex
            marker = self.path.with_name(self.path.name + ".releases") / (identifier + ".json")
            LeaseRegistry(marker)._assert_path_safe()
            for attempt in range(3):
                try:
                    _write_report(marker, {"session": session, "turn": turn, "id": identifier})
                    break
                except OSError:
                    if attempt == 2:
                        raise RuntimeError("release-not-persisted") from None
                    time.sleep(0.05)
            last = None
            for attempt in range(3):
                try:
                    with self.lock._exclusive_lock():
                        data = self._read()
                        releases = self._release_files(data)
                        self._write(data)
                        self._ack(releases)
                    return
                except (OSError, LeaseRegistryError) as exc:
                    last = exc
                    if attempt < 2:
                        time.sleep(0.05)
            raise RuntimeError("release-pending-replay") from last
        with self.lock._exclusive_lock():
            data = self._read()
            data["rows"] = [r for r in data["rows"] if not (
                r["session"] == session and (turn is None or r["turn"] == turn)
                and (not waiting_only or r["state"] == "waiting"))]
            self._write(data)

    def bind_child(self, parent: str, child: str) -> None:
        with self.lock._exclusive_lock():
            data = self._read()
            releases = self._release_files(data)
            parent = data["children"].get(parent, parent)
            if (not any(r["session"] == parent and r["state"] == "active" for r in data["rows"])
                    or len(data["children"]) >= MAX_ROWS):
                raise ValueError("parent-not-admitted")
            data["children"][child] = parent
            self._write(data)
            self._ack(releases)

    def child_admitted(self, session: str, agent: str) -> bool:
        with self.lock._exclusive_lock():
            data = self._read()
            releases = self._release_files(data)
            parent = data["children"].get(agent)
            allowed = parent is not None and session in (parent, agent) and any(
                r["session"] == parent and r["state"] == "active" and alive(r["backend"]) for r in data["rows"])
            self._write(data)
            self._ack(releases)
            return allowed

    def acquire(self, session: str, turn: str, backend: list, *, wait_seconds: float = 3600) -> bool:
        if not math.isfinite(wait_seconds) or not 0 < wait_seconds <= 3600:
            raise ValueError("invalid-wait-budget")
        waiter = [os.getpid(), psutil.Process().create_time()]
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            if self.try_admit(session, turn, backend, waiter):
                return True
            time.sleep(min(0.5, max(0, deadline - time.monotonic())))
        self.release(session, turn, waiting_only=True)
        return False


def event(gate: Gate, payload: dict) -> bool:
    name = payload.get("hook_event_name", payload.get("type"))
    session = key(payload.get("session_id", payload.get("thread-id")))
    if name == "SubagentStart":
        gate.bind_child(session, key(payload.get("agent_id")))
        return True
    if payload.get("agent_id"):
        return gate.child_admitted(session, key(payload["agent_id"]))
    if name == "SessionEnd":
        raise ValueError("session-end-without-exact-turn-is-not-release-authority")
    turn = key(payload.get("turn_id", payload.get("turn-id")))
    if name in ("agent-turn-complete", "Interrupt"):
        gate.release(session, turn)
        return True
    if name == "UserPromptSubmit":
        return gate.acquire(session, turn, runtime_owner())
    raise ValueError("unsupported-admission-event")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--previous-notify", type=Path)
    parser.add_argument("payload", nargs="?")
    args = parser.parse_args(argv)
    raw = args.payload if args.payload is not None else sys.stdin.read(1048577)
    ok = False
    try:
        if len(raw.encode("utf-8")) > 1048576:
            raise ValueError("oversize-hook-input")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("invalid-hook-input")
        ok = event(Gate(args.state), payload)
    except (ValueError, KeyError, TypeError, RuntimeError, OSError, LeaseRegistryError, psutil.Error):
        ok = False
    if args.previous_notify is not None:
        # Preserve the prior notification chain independently of release success.
        try:
            previous = json.loads(args.previous_notify.read_text(encoding="utf-8"))
            if (not isinstance(previous, list) or not previous
                    or not all(isinstance(x, str) and x for x in previous)):
                raise ValueError("invalid-previous-notify")
            subprocess.Popen([*previous, raw], stdin=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, ValueError):
            return 1
        return 0 if ok else 1
    if not ok:
        print(json.dumps({"decision": "block", "reason": "同時実行ガードで待機期限または検査エラー。状態を確認してください。"}, ensure_ascii=True))
        return 0  # explicit blocking decision, not a fail-open hook error
    print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
