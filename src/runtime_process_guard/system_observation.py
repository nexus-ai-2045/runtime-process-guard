"""全体メタデータの読取り。観測から所有・停止権限を作らない。"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import socket
import sqlite3
import time

import psutil

from . import inventory

SCHEMA = "runtime-process-guard/system-inventory-v1"


def _previous(path, current_host: str | None) -> dict | None:
    if not path.exists():
        return None
    value = inventory.read_json(path)
    coverage = value.get("coverage")
    rows = value.get("processes")
    if (value.get("schema_version") != SCHEMA or value.get("owner") != "system"
            or not inventory._finite(value.get("sample_epoch"))
            or not 0 < value["sample_epoch"] <= time.time()
            or not inventory._finite(value.get("sample_monotonic"))
            or value["sample_monotonic"] < 0
            or (current_host is not None and value.get("host_session") == current_host
                and value["sample_monotonic"] > time.monotonic())
            or not (value.get("host_session") is None or isinstance(value["host_session"], str))
            or not isinstance(coverage, dict) or coverage.get("scope") != "system"
            or not isinstance(rows, list)
            or coverage.get("observed") != len(rows)
            or any(type(coverage.get(k)) is not int or coverage[k] < 0
                   for k in ("observed", "inaccessible", "truncated"))
            or coverage.get("enumeration") != "os-best-effort"):
        raise ValueError("invalid-system-cache")
    seen = set()
    for row in rows:
        if (not isinstance(row, dict) or type(row.get("pid")) is not int or row["pid"] <= 0
                or row["pid"] in seen or not isinstance(row.get("name"), str)
                or not inventory._finite(row.get("created_at"))
                or not 0 < row["created_at"] <= value["sample_epoch"]
                or not inventory._finite(row.get("cpu_seconds")) or row["cpu_seconds"] < 0):
            raise ValueError("invalid-system-cache-process")
        seen.add(row["pid"])
        row["name"] = inventory._safe_text(row["name"])
        row["kind"] = inventory._kind(row["name"])
    return value


def host_session() -> str | None:
    """端末名そのものは出さず、端末とOS起動の組を識別する。"""
    try:
        boot = psutil.boot_time()
        host = socket.gethostname()
        if not inventory._finite(boot) or boot <= 0 or not host:
            return None
        return hashlib.sha256(f"{host}:{boot}".encode()).hexdigest()
    except (OSError, psutil.Error):
        return None


def _forest(processes: list[dict]) -> tuple[list[dict], list[int]]:
    by_pid = {p["pid"]: {**p, "name": inventory._safe_text(p["name"])} for p in processes}
    parents = {pid: p["ppid"] if p["ppid"] in by_pid
               and by_pid[p["ppid"]]["created_at"] <= p["created_at"]
               and p["ppid"] != pid else None for pid, p in by_pid.items()}
    done = set()
    for start in by_pid:
        trail, positions = [], {}
        pid = start
        while pid is not None and pid not in done and pid not in positions:
            positions[pid] = len(trail)
            trail.append(pid)
            pid = parents[pid]
        if pid in positions:
            # 同時刻の循環を帰属証拠にしない。循環の全辺を切る。
            for member in trail[positions[pid]:]:
                parents[member] = None
        done.update(trail)
    normalized = [{**p, "ppid": parents[pid] or 0} for pid, p in by_pid.items()]
    return normalized, sorted(pid for pid, parent in parents.items() if parent is None)


def build_report(processes: list[dict], bindings: list[dict], *, now: float,
                 sample_monotonic: float, host: str | None, inaccessible: int = 0,
                 previous: dict | None = None, titles: dict | None = None) -> dict:
    normalized, roots = _forest(processes)
    coverage = {"scope": "system", "observed": len(normalized),
                "inaccessible": inaccessible, "truncated": 0,
                "status": "partial" if inaccessible else "observed",
                "enumeration": "os-best-effort"}
    continuous = bool(previous and host and previous.get("host_session") == host
                      and inventory._finite(previous.get("sample_monotonic"))
                      and sample_monotonic > previous["sample_monotonic"])
    baseline = None
    if continuous:
        baseline = {**previous, "sample_epoch": now - (sample_monotonic - previous["sample_monotonic"])}
    report = inventory.build_report(normalized, roots, bindings, now=now,
                                    previous=baseline, titles=titles)
    original_parents = {p["pid"]: p["ppid"] for p in processes}
    for row in report["processes"]:
        row["ppid"] = original_parents[row["pid"]]
    comparable = bool(continuous and previous.get("coverage", {}).get("scope") == "system"
                      and all(previous.get("coverage", {}).get(key) == coverage[key]
                              for key in ("inaccessible", "truncated", "enumeration")))
    # 部分観測同士も欠落PIDを特定できないため増殖の根拠にはしない。
    comparable = comparable and inaccessible == 0
    if not comparable:
        report["diff"] = {"baseline_available": False, "added_pids": [],
                          "removed_pids": [], "stable_count": 0}
    report["diff"]["comparison_status"] = "comparable" if comparable else "unverified"
    old_counts = Counter((inventory._safe_text(p["name"]), inventory._kind(inventory._safe_text(p["name"])))
                         for p in (previous or {}).get("processes", []))
    groups = {}
    for row in report["processes"]:
        key = (row["name"], row["kind"])
        group = groups.setdefault(key, {"name": key[0], "kind": key[1], "pids": [],
                                       "process_count": 0, "rss_bytes": 0,
                                       "cpu_percent": 0.0, "cpu_complete": True})
        group["pids"].append(row["pid"])
        group["process_count"] += 1
        group["rss_bytes"] += row["rss_bytes"]
        group["cpu_complete"] &= row["cpu_percent"] is not None
        group["cpu_percent"] += row["cpu_percent"] or 0
    old_rows = {(p["pid"], p["created_at"]): p for p in (previous or {}).get("processes", [])}
    new_rows = {(p["pid"], p["created_at"]): p for p in report["processes"]}
    added = Counter((r["name"], r["kind"]) for k, r in new_rows.items() if k not in old_rows)
    removed = Counter((r["name"], r["kind"]) for k, r in old_rows.items() if k not in new_rows)
    for key, group in groups.items():
        group["count_delta"] = (group["process_count"] - old_counts[key]
                                if comparable else None)
        group["added_count"] = added[key] if comparable else None
        group["removed_count"] = removed[key] if comparable else None
    # 消滅した種類も0本として示す。
    if comparable:
        for key, old_count in old_counts.items():
            if key not in groups:
                groups[key] = {"name": inventory._safe_text(key[0]), "kind": key[1], "pids": [],
                               "process_count": 0, "rss_bytes": 0, "cpu_percent": 0.0,
                               "cpu_complete": True, "count_delta": -old_count,
                               "added_count": 0, "removed_count": old_count}
    report.update(schema_version=SCHEMA, owner="system", scope="system", host_session=host,
                  sample_monotonic=sample_monotonic, coverage=coverage,
                  collection_complete=inaccessible == 0, inaccessible_processes=inaccessible,
                  owner_found=True, process_groups=sorted(groups.values(), key=lambda g: -g["rss_bytes"]))
    report["totals"]["count_delta"] = len(new_rows) - len(old_rows) if comparable else None
    report["limitations"].append("OSの列挙範囲の部分和。増加はリーク・不要性・停止許可を示さない。")
    return report


def snapshot(args: argparse.Namespace) -> dict:
    from .cli import _try_acquire_feedback_lock, _release_feedback_lock

    lock = _try_acquire_feedback_lock(args.state)
    if lock is None:
        raise ValueError("inventory-writer-busy-or-lock-invalid")
    wall_start, cpu_start = time.perf_counter(), time.process_time()
    try:
        for source in (args.bindings, args.threads_db):
            if source is not None and source.resolve() == args.state.resolve():
                raise ValueError("state-cannot-overwrite-input")
        host = host_session()
        previous = _previous(args.state, host)
        bindings, binding_status = [], "not-configured"
        if args.bindings is not None:
            try:
                bindings = inventory.validate_bindings(inventory.read_json(args.bindings))
                binding_status = "readable"
            except (OSError, ValueError, KeyError, TypeError):
                binding_status = "unavailable"
        processes, inaccessible = inventory.collect_processes()
        report = build_report(processes, bindings, now=time.time(), sample_monotonic=time.monotonic(),
                              host=host, inaccessible=inaccessible, previous=previous)
        catalog, catalog_status = [], "not-configured"
        if args.threads_db is not None:
            try:
                catalog = inventory.registered_chats(args.threads_db, limit=1000, report=report)
                catalog_status = "readable"
            except (OSError, ValueError, sqlite3.Error):
                catalog_status = "unavailable"
        titles = {c["thread_id"]: c["title"] for c in catalog}
        for group in report["chat_groups"]:
            group["title"] = inventory._safe_text(titles.get(group["key"], group["title"]))
        for row in report["processes"]:
            row["related_chat_titles"] = [titles.get(tid, "名称未取得") for tid in row["related_thread_ids"]]
        report.update(registered_chats=catalog, catalog_status=catalog_status,
                      catalog_limit=1000, catalog_may_be_truncated=len(catalog) == 1000,
                      bindings_status=binding_status, thread_titles_readable=catalog_status == "readable")
        report["collector"] = {"wall_ms": round(1000 * (time.perf_counter() - wall_start), 3),
                               "cpu_ms": round(1000 * (time.process_time() - cpu_start), 3),
                               "rss_bytes": psutil.Process().memory_info().rss,
                               "processes_scanned": len(processes), "metadata_only": True}
        inventory._write(args.state, report)
        return report
    finally:
        _release_feedback_lock(lock)
