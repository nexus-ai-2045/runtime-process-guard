"""Read-only process observatory. No hooks, signals, archives or background services."""

from __future__ import annotations

import argparse
from collections import defaultdict, deque
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import time

import psutil

from .lineage import compare_lineage_reports
from .privacy import redact_text

SCHEMA = "runtime-process-guard/inventory-v2"
BINDINGS = "runtime-process-guard/chat-bindings-v2"
MARKER = "runtime-process-guard/live-marker-v1"
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
MAX_BYTES = 8 * 1024 * 1024


def _finite(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _safe_text(value: str, limit: int = 120) -> str:
    return redact_text("".join(c if c.isprintable() else " " for c in value))[:limit]


def _read_bytes(path: Path, *, private: bool = False, limit: int = MAX_BYTES) -> tuple[bytes, os.stat_result]:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > limit:
            raise ValueError("unsafe-or-oversized-input")
        if private and os.name == "posix" and (st.st_uid != os.getuid() or st.st_mode & 0o077):
            raise ValueError("marker-must-be-private-and-user-owned")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            raw = handle.read(limit + 1)
        after = os.fstat(fd)
        if len(raw) > limit or (st.st_size, st.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("oversized-or-changing-input")
        return raw, st
    finally:
        os.close(fd)


def read_json(path: Path) -> dict:
    raw, _ = _read_bytes(path)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("expected-json-object")
    return value


def _write(path: Path, value: dict) -> None:
    # Reuse the repository's atomic writer; its temporary files are private.
    from .cli import _write_report

    if path.is_symlink():
        raise ValueError("refuse-symlink-output")
    _write_report(path, value)
    if os.name == "posix":
        path.chmod(0o600)


def _kind(name: str) -> str:
    name = name.lower()
    if "renderer" in name:
        return "renderer"
    if "node_repl" in name:
        return "mcp/node_repl"
    if "mcp" in name:
        return "mcp"
    if name in ("node", "node.exe", "bun", "bun.exe"):
        return "node"
    return "other"


def collect_processes() -> tuple[list[dict], int]:
    """One cheap enumeration: never inspect arguments, environment or open files."""
    result = []
    inaccessible = 0
    attrs = ["pid", "ppid", "name", "create_time", "memory_info", "cpu_times"]
    for process in psutil.process_iter(attrs):
        try:
            p = process.info
            if (type(p["pid"]) is not int or type(p["ppid"]) is not int
                    or not isinstance(p["name"], str) or not p["name"]
                    or not _finite(p["create_time"]) or p["create_time"] <= 0
                    or p["memory_info"] is None or p["cpu_times"] is None):
                inaccessible += 1
                continue
            result.append({
                "pid": p["pid"], "ppid": p["ppid"],
                "name": _safe_text(p["name"]), "created_at": p["create_time"],
                "rss_bytes": p["memory_info"].rss,
                "cpu_seconds": p["cpu_times"].user + p["cpu_times"].system,
            })
        except (psutil.Error, OSError):
            inaccessible += 1
    return result, inaccessible


def validate_bindings(value: dict) -> list[dict]:
    if value.get("schema_version") != BINDINGS or not isinstance(value.get("bindings"), list):
        raise ValueError("invalid-binding-registry")
    for b in value["bindings"]:
        if (not isinstance(b, dict) or type(b.get("pid")) is not int or b["pid"] <= 0
                or not _finite(b.get("created_at")) or b["created_at"] <= 0
                or not isinstance(b.get("thread_id"), str) or not UUID.fullmatch(b["thread_id"])
                or b.get("method") != "live-marker-and-os-fd"
                or b.get("scope") != "execution-kernel"
                or type(b.get("server_pid")) is not int or b["server_pid"] <= 0
                or b["server_pid"] == b["pid"]
                or not _finite(b.get("server_created_at")) or b["server_created_at"] <= 0
                or not _finite(b.get("verified_at"))
                or not _finite(b.get("expires_at"))
                or b["expires_at"] <= b["verified_at"]
                or not isinstance(b.get("evidence_sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", b["evidence_sha256"])):
            raise ValueError("invalid-binding-entry")
    return value["bindings"]


def chat_titles(database: Path | None, thread_ids: set[str]) -> tuple[dict, bool]:
    """Resolve only observed thread IDs. Never scan conversation bodies or logs."""
    if database is None or not thread_ids:
        return {}, database is not None
    try:
        with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.2) as c:
            columns = {row[1] for row in c.execute("PRAGMA table_info(threads)")}
            name_columns = [name for name in ("name", "title", "agent_nickname") if name in columns]
            if not name_columns:
                return {}, False
            return {
                tid: _safe_text(next((v for v in values if isinstance(v, str) and v.strip()), "名称未設定"))
                for tid in sorted(thread_ids)
                for values in c.execute("SELECT " + ",".join(name_columns) + " FROM threads WHERE id=?", (tid,))
            }, True
    except (sqlite3.Error, OSError, ValueError):
        return {}, False


def registered_chats(database: Path, *, limit: int = 50, report: dict | None = None) -> list[dict]:
    """On-demand metadata catalog. Name availability and process attribution are separate."""
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("chat-limit-must-be-1-to-1000")
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.2) as c:
        columns = {row[1] for row in c.execute("PRAGMA table_info(threads)")}
        names = [name for name in ("name", "title", "agent_nickname") if name in columns]
        if not names or "id" not in columns:
            raise ValueError("thread-catalog-schema-unavailable")
        where = " WHERE archived=0" if "archived" in columns else ""
        order = " ORDER BY updated_at DESC,id" if "updated_at" in columns else " ORDER BY id"
        query = "SELECT id," + ",".join(names) + " FROM threads" + where + order + " LIMIT ?"
        metadata = c.execute(query, (limit,)).fetchall()
    rows = (report or {}).get("processes", [])
    result = []
    for tid, *values in metadata:
        # The catalog's name is independent of any process evidence.
        title = _safe_text(next((v for v in values if isinstance(v, str) and v.strip()), "名称未設定"))
        direct = [p["pid"] for p in rows if tid in p["thread_ids"]]
        related = [p["pid"] for p in rows if tid in p.get("related_thread_ids", [])]
        result.append({"thread_id": tid, "title": title, "attributed_pids": direct,
                       "related_pids": related, "process_mapping": "observed" if direct else
                       "related-only" if related else "unverified"})
    return result


def build_report(processes: list[dict], owner_pids: list[int], bindings: list[dict],
                 *, now: float, previous: dict | None = None, titles: dict | None = None) -> dict:
    """Pure grouping. Parent ancestry never proves a binding for sibling processes."""
    titles = {tid: _safe_text(title) for tid, title in (titles or {}).items()}
    by_pid = {p["pid"]: dict(p) for p in processes}
    children: dict[int, list[int]] = defaultdict(list)
    for p in processes:
        parent = by_pid.get(p["ppid"])
        if parent and parent["created_at"] <= p["created_at"] and p["pid"] != p["ppid"]:
            children[p["ppid"]].append(p["pid"])
    roots = sorted(set(owner_pids) & by_pid.keys())
    selected: dict[int, int | None] = {}
    queue = deque((pid, None) for pid in roots)
    order = []
    while queue:
        pid, parent = queue.popleft()
        if pid in selected:
            continue
        selected[pid] = parent
        order.append(pid)
        queue.extend((child, pid) for child in sorted(children[pid]))
    direct: dict[int, set[str]] = defaultdict(set)
    rejected = 0
    for b in bindings:
        p = by_pid.get(b["pid"])
        server = by_pid.get(b.get("server_pid"))
        if (p is None or p["created_at"] != b["created_at"]
                or b.get("scope") != "execution-kernel" or _kind(p["name"]) != "node"
                or server is None or _kind(server["name"]) != "mcp/node_repl"
                or server["created_at"] != b.get("server_created_at") or p["ppid"] != server["pid"]
                or b["expires_at"] <= now or b["verified_at"] > now):
            rejected += 1
            continue
        direct[b["pid"]].add(b["thread_id"])
    old_rows = {
        (p["pid"], p["created_at"]): p for p in (previous or {}).get("processes", [])
    }
    elapsed = now - previous["sample_epoch"] if previous else 0
    rows = []
    inherited: dict[int, set[str]] = {}
    for pid in order:
        p = by_pid[pid]
        parent = selected[pid]
        # An independently observed child binding overrides an inferred ancestor binding.
        tids = direct.get(pid) or inherited.get(parent, set())
        inherited[pid] = tids
        old = old_rows.get((pid, p["created_at"]))
        cpu = None
        if old and elapsed > 0 and p["cpu_seconds"] >= old["cpu_seconds"]:
            cpu = round(100 * (p["cpu_seconds"] - old["cpu_seconds"]) / elapsed, 2)
        status = "shared" if len(tids) > 1 else "attributed" if tids else "unknown"
        rows.append({**p, "parent_pid": parent, "kind": _kind(p["name"]),
                     "thread_ids": sorted(tids), "attribution": status,
                     "kind_basis": "executable-name-only",
                     "binding_scope": "direct-observation" if pid in direct else
                         "observed-descendant" if tids else "unverified",
                     "cpu_percent": cpu, "usage_state": "unknown"})
    groups: dict[str, dict] = {}
    for p in rows:
        tids = p["thread_ids"]
        key = tids[0] if len(tids) == 1 else "shared" if tids else "unknown"
        group = groups.setdefault(key, {"key": key, "title": (titles or {}).get(key,
                                  "共有" if key == "shared" else "未特定" if key == "unknown" else "名称未取得"),
                                  "pids": [], "rss_bytes": 0, "cpu_percent": 0.0,
                                  "cpu_complete": True})
        group["pids"].append(p["pid"])
        group["rss_bytes"] += p["rss_bytes"]
        if p["cpu_percent"] is None:
            group["cpu_complete"] = False
        else:
            group["cpu_percent"] += p["cpu_percent"]
    subtree = {p["pid"]: {"process_count": 1, "rss_bytes": p["rss_bytes"]} for p in rows}
    for pid in reversed(order):
        parent = selected[pid]
        if parent is not None:
            for key in ("process_count", "rss_bytes"):
                subtree[parent][key] += subtree[pid][key]
    for p in rows:
        p["subtree"] = subtree[p["pid"]]
        p["related_thread_ids"] = []
    # A tool observation identifies its execution kernel, not every kernel sharing
    # its transport. Expose the transport relation without assigning its resources.
    row_by_pid = {p["pid"]: p for p in rows}
    for p in rows:
        if p["binding_scope"] != "direct-observation":
            continue
        parent = row_by_pid.get(p["parent_pid"])
        if parent is not None and parent["kind"] == "mcp/node_repl":
            parent["related_thread_ids"] = sorted(set(parent["related_thread_ids"]) | set(p["thread_ids"]))
    for p in rows:
        p["related_chat_titles"] = [(titles or {}).get(tid, "名称未取得")
                                    for tid in p["related_thread_ids"]]
    report = {"schema_version": SCHEMA, "read_only": True, "sample_epoch": now,
              "observed_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
              "owner_pids": roots, "processes": rows,
              "totals": {"process_count": len(rows), "rss_bytes": sum(p["rss_bytes"] for p in rows),
                         "cpu_percent": round(sum(p["cpu_percent"] or 0 for p in rows), 2),
                         "cpu_complete": all(p["cpu_percent"] is not None for p in rows)},
              "chat_groups": sorted(groups.values(), key=lambda g: -g["rss_bytes"]),
              "binding_rejections": rejected,
              "limitations": ["RSSの算術和。共有メモリを含み、圧縮・swapは含まない。",
                              "接続中・処理中・停止可能性は未確認。対応記録は停止権限ではない。",
                              "初回のCPU値は未測定。子孫合計を足し直すと二重計上になる。"]}
    report["diff"] = compare_lineage_reports(report, previous)
    return report


def _previous(path: Path) -> dict | None:
    if not path.exists():
        return None
    r = read_json(path)
    if r.get("schema_version") != SCHEMA or not _finite(r.get("sample_epoch")):
        raise ValueError("invalid-inventory-cache")
    # Older v2 caches remain readable, but their display metadata is not trusted.
    for group in r.get("chat_groups", []):
        group["title"] = _safe_text(group["title"])
    for process in r.get("processes", []):
        process["name"] = _safe_text(process["name"])
        process["related_chat_titles"] = [_safe_text(title) for title in process.get("related_chat_titles", [])]
    return r


def snapshot(args: argparse.Namespace) -> dict:
    from .cli import _try_acquire_feedback_lock, _release_feedback_lock

    lock = _try_acquire_feedback_lock(args.state)
    if lock is None:
        raise ValueError("inventory-writer-busy-or-lock-invalid")
    start = time.perf_counter()
    cpu_start = time.process_time()
    try:
        previous = _previous(args.state)
        if previous is not None and previous.get("owner") != args.owner:
            raise ValueError("inventory-owner-changed-use-separate-state")
        for source in (args.bindings, args.threads_db):
            if source is not None and source.resolve() == args.state.resolve():
                raise ValueError("state-cannot-overwrite-input")
        binding_rows = validate_bindings(read_json(args.bindings)) if args.bindings and args.bindings.exists() else []
        processes, inaccessible = collect_processes()
        owner_pids = [p["pid"] for p in processes if p["name"].lower() == args.owner.lower()]
        names, title_readable = chat_titles(args.threads_db, {b["thread_id"] for b in binding_rows})
        report = build_report(processes, owner_pids, binding_rows, now=time.time(), previous=previous, titles=names)
        report.update(owner=args.owner, inaccessible_processes=inaccessible,
                      thread_titles_readable=title_readable, collection_complete=inaccessible == 0,
                      owner_found=bool(owner_pids))
        report["collector"] = {"wall_ms": round(1000 * (time.perf_counter() - start), 3),
                               "cpu_ms": round(1000 * (time.process_time() - cpu_start), 3),
                               "rss_bytes": psutil.Process().memory_info().rss,
                               "processes_scanned": len(processes),
                               "metadata_only": True}
        _write(args.state, report)
        return report
    finally:
        _release_feedback_lock(lock)


def _kernel_holds_inode(kernel: psutil.Process, marker: Path, st: os.stat_result) -> bool:
    for f in kernel.open_files():
        if Path(f.path).resolve() != marker.resolve():
            continue
        if sys.platform.startswith("linux"):
            opened = os.stat(f"/proc/{kernel.pid}/fd/{f.fd}")
            if (opened.st_dev, opened.st_ino) == (st.st_dev, st.st_ino):
                return True
        elif sys.platform == "darwin":
            # Registration only: inspect inode/device, never command lines or file contents.
            r = subprocess.run(["/usr/sbin/lsof", "-nP", "-a", "-p", str(kernel.pid),
                                "-d", str(f.fd), "-F", "fiD"],
                               capture_output=True, text=True, timeout=3)
            fields = {line[0]: line[1:] for line in r.stdout.splitlines() if line}
            if (r.returncode == 0 and fields.get("i") == str(st.st_ino)
                    and fields.get("D") and int(fields["D"], 16) == st.st_dev):
                return True
    return False


def bind_marker(args: argparse.Namespace) -> dict:
    """Explicit registration of a live tool observation; no ownership or stop rights."""
    from .cli import _try_acquire_feedback_lock, _release_feedback_lock

    if args.bindings.resolve() == args.marker.resolve():
        raise ValueError("bindings-cannot-overwrite-marker")
    original_marker, st = _read_bytes(args.marker, private=True, limit=4096)
    m = json.loads(original_marker)
    if not isinstance(m, dict):
        raise ValueError("invalid-live-marker-object")
    if (not stat.S_ISREG(st.st_mode) or st.st_size > 4096
            or (os.name == "posix" and (stat.S_IMODE(st.st_mode) & 0o077 or st.st_uid != os.getuid()))
            or m.get("schema_version") != MARKER
            or not isinstance(m.get("thread_id"), str) or not UUID.fullmatch(m["thread_id"])
            or not isinstance(m.get("nonce"), str) or not UUID.fullmatch(m["nonce"])
            or not _finite(m.get("observed_epoch"))
            or not 0 <= time.time() - m["observed_epoch"] <= 60):
        raise ValueError("invalid-or-stale-live-marker")
    kernel = psutil.Process(args.kernel_pid)
    kernel_birth = kernel.create_time()
    if _kind(kernel.name()) != "node" or not _kernel_holds_inode(kernel, args.marker, st):
        raise ValueError("marker-not-open-in-kernel")
    server = kernel.parent()
    if server is None or "node_repl" not in server.name().lower():
        raise ValueError("kernel-not-child-of-node-repl")
    server_birth = server.create_time()
    if (kernel.create_time() != kernel_birth or kernel.ppid() != server.pid
            or server.create_time() != server_birth):
        raise ValueError("process-generation-changed")
    lock = _try_acquire_feedback_lock(args.bindings)
    if lock is None:
        raise ValueError("binding-writer-busy-or-lock-invalid")
    try:
        existing = validate_bindings(read_json(args.bindings)) if args.bindings.exists() else []
        now = time.time()
        existing = [b for b in existing if b["expires_at"] > now]
        entry = {"pid": kernel.pid, "created_at": kernel_birth, "thread_id": m["thread_id"],
                 "server_pid": server.pid, "server_created_at": server_birth,
                 "scope": "execution-kernel",
                 "verified_at": now, "expires_at": now + args.ttl,
                 "method": "live-marker-and-os-fd",
                 "evidence_sha256": hashlib.sha256(original_marker).hexdigest()}
        current_raw, current_st = _read_bytes(args.marker, private=True, limit=4096)
        if (current_raw != original_marker or (current_st.st_dev, current_st.st_ino) != (st.st_dev, st.st_ino)
                or kernel.create_time() != kernel_birth or kernel.ppid() != server.pid
                or server.create_time() != server_birth):
            raise ValueError("marker-or-process-changed-before-registration")
        existing = [b for b in existing if (b["pid"], b["created_at"], b["thread_id"]) !=
                    (entry["pid"], entry["created_at"], entry["thread_id"])]
        value = {"schema_version": BINDINGS, "bindings": existing + [entry],
                 "trust_boundary": "操作者が実ツールのrequestMetaと照合して採用する観測記録。署名・専有・停止許可の証明ではない。"}
        _write(args.bindings, value)
        return {"registered": True, "pid": kernel.pid, "created_at": kernel_birth,
                "related_server_pid": server.pid,
                "thread_id": entry["thread_id"], "method": entry["method"], "stop_allowed": False}
    finally:
        _release_feedback_lock(lock)


def render(report: dict, *, tree: bool = False) -> str:
    age = max(0, time.time() - report["sample_epoch"])
    total = report["totals"]
    lines = [f"観測から {age:.1f}秒 / {total['process_count']}プロセス / RSS合計 {total['rss_bytes']/2**20:.2f} MiB"]
    lines.append("PID     種類              RSS(MiB) CPU(%) チャット / 対応根拠")
    if tree:
        titles = {g["key"]: g["title"] for g in report["chat_groups"]}
        by_parent = defaultdict(list)
        for row in report["processes"]:
            by_parent[row["parent_pid"]].append(row)
        pending = [(p, 0) for p in sorted(by_parent[None], key=lambda p: p["subtree"]["rss_bytes"])]
        while pending:
                p, depth = pending.pop()
                tids = p["thread_ids"]
                title = titles.get(tids[0], "名称未取得") if len(tids) == 1 else "共有" if tids else "未特定"
                if not tids and p["related_chat_titles"]:
                    title = "・".join(p["related_chat_titles"]) + "（関連のみ・専有未確認）"
                cpu = "未測定" if p["cpu_percent"] is None else str(p["cpu_percent"])
                lines.append(f"{'  '*min(depth, 16)}{p['pid']:<7} {p['kind']:<17} {p['rss_bytes']/2**20:8.2f} {cpu:>6} {title} / {p['binding_scope']} / 配下{p['subtree']['rss_bytes']/2**20:.2f}MiB")
                pending.extend((child, depth + 1) for child in
                               sorted(by_parent[p["pid"]], key=lambda p: p["subtree"]["rss_bytes"]))
    else:
        for group in report["chat_groups"]:
            cpu = f"{group['cpu_percent']:.1f}" if group["cpu_complete"] else "未測定/部分値"
            lines.append(f"{group['title']} | {len(group['pids'])}本 | {group['rss_bytes']/2**20:.2f} MiB | CPU {cpu}")
    lines.extend(report["limitations"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="runtime-process-guard inventory")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("snapshot", "watch"):
        p = commands.add_parser(name)
        p.add_argument("--owner", required=True)
        p.add_argument("--state", type=Path, required=True)
        p.add_argument("--bindings", type=Path)
        p.add_argument("--threads-db", type=Path)
        p.add_argument("--tree", action="store_true")
        p.add_argument("--json", action="store_true")
        if name == "watch":
            p.add_argument("--interval", type=float, default=30)
            p.add_argument("--samples", type=int, default=2)
    p = commands.add_parser("show")
    p.add_argument("--state", type=Path, required=True)
    p.add_argument("--tree", action="store_true")
    p.add_argument("--json", action="store_true")
    p = commands.add_parser("chats")
    p.add_argument("--threads-db", type=Path, required=True)
    p.add_argument("--state", type=Path)
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--json", action="store_true")
    p = commands.add_parser("bind")
    p.add_argument("--marker", type=Path, required=True)
    p.add_argument("--kernel-pid", type=int, required=True)
    p.add_argument("--bindings", type=Path, required=True)
    p.add_argument("--ttl", type=float, default=1800)
    p = commands.add_parser("marker-script")
    p.add_argument("--marker", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "chats":
            report = _previous(args.state) if args.state else None
            catalog = registered_chats(args.threads_db, limit=args.limit, report=report)
            if args.json:
                print(json.dumps({"registered_chats": catalog,
                                  "process_observed_at": (report or {}).get("observed_at"),
                                  "limit": args.limit, "read_only": True}, ensure_ascii=False))
            else:
                print("登録チャット名と、保存時点のPID対応。未対応は名前の取得失敗ではありません。")
                print("PID観測時刻: " + (report or {}).get("observed_at", "観測なし"))
                for chat in catalog:
                    print(f"{chat['title']} | ID {chat['thread_id']} | "
                          f"対応PID {chat['attributed_pids']} | 関連PID {chat['related_pids']} | {chat['process_mapping']}")
            return 0
        if args.command == "marker-script":
            # Execute in the desired chat's node_repl, then use lsof on this file.
            path = json.dumps(str(args.marker), ensure_ascii=False)
            print("var rpgFs = await import('node:fs/promises'); var rpgCrypto = await import('node:crypto'); "
                  f"var rpgMarker = await rpgFs.open({path}, 'wx', 0o600); "
                  f"await rpgMarker.writeFile(JSON.stringify({{schema_version:'{MARKER}', "
                  "thread_id:nodeRepl.requestMeta.threadId, nonce:rpgCrypto.randomUUID(), observed_epoch:Date.now()/1000})); "
                  "nodeRepl.write({threadId:nodeRepl.requestMeta.threadId,markerHeldOpen:true});")
            return 0
        if args.command == "bind":
            if not _finite(args.ttl) or not 0 < args.ttl <= 86400:
                raise ValueError("ttl-must-be-between-zero-and-86400")
            print(json.dumps(bind_marker(args), ensure_ascii=False))
            return 0
        if args.command == "show":
            report = _previous(args.state)
            if report is None:
                raise ValueError("inventory-cache-missing-run-snapshot")
            print(json.dumps(report, ensure_ascii=False) if args.json else render(report, tree=args.tree))
            return 0
        samples = args.samples if args.command == "watch" else 1
        interval = args.interval if args.command == "watch" else 30
        if not _finite(interval) or interval < 5 or not 1 <= samples <= 120:
            raise ValueError("watch-requires-interval-at-least-5s-and-1-to-120-samples")
        for index in range(samples):
            if index:
                time.sleep(interval)
            report = snapshot(args)
            print(json.dumps(report, ensure_ascii=False) if args.json else render(report, tree=args.tree), flush=True)
        return 0 if report["owner_found"] else 40
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, psutil.Error, subprocess.TimeoutExpired):
        # Never echo exception details containing command arguments or private paths.
        print(json.dumps({"overall": "unknown", "read_only": True,
                          "next_action": "入力・観測権限・既存状態を確認。未確認の対応を採用しない。"}, ensure_ascii=False))
        return 40


if __name__ == "__main__":
    sys.exit(main())
