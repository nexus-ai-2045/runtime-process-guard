"""Privacy-safe process lineage snapshots and Mermaid rendering."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import re
import time

import psutil

from .privacy import command_identity


@dataclass(frozen=True)
class LineageRow:
    pid: int
    ppid: int
    name: str
    created_at: str
    depth: int
    identity_prefix: str
    recent: bool


def _safe_node_id(pid: int) -> str:
    return f"p{pid}"


def _safe_label(row: LineageRow) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]", "?", row.name)
    marker = " NEW" if row.recent else ""
    return f"{name} PID {row.pid}{marker}"


def compare_lineage_reports(
    current: dict[str, object], previous: dict[str, object] | None
) -> dict[str, object]:
    """Compare snapshots using PID plus creation time to avoid PID-reuse mistakes."""
    current_rows = current.get("processes", [])
    previous_rows = previous.get("processes", []) if previous else []
    current_keys = {
        (int(row["pid"]), str(row["created_at"])) for row in current_rows if isinstance(row, dict)
    }
    previous_keys = {
        (int(row["pid"]), str(row["created_at"])) for row in previous_rows if isinstance(row, dict)
    }
    return {
        "baseline_available": previous is not None,
        "added_pids": sorted(pid for pid, _created in current_keys - previous_keys),
        "removed_pids": sorted(pid for pid, _created in previous_keys - current_keys),
        "stable_count": len(current_keys & previous_keys),
    }


def render_mermaid(
    rows: list[LineageRow], owner_pids: list[int], *, added_pids: set[int] | None = None
) -> str:
    by_pid = {row.pid: row for row in rows}
    additions = added_pids or set()
    lines = ["```mermaid", "flowchart TD"]
    for row in sorted(rows, key=lambda item: (item.depth, item.created_at, item.pid)):
        lines.append(f'  {_safe_node_id(row.pid)}["{_safe_label(row)}"]')
        if row.ppid in by_pid:
            lines.append(f"  {_safe_node_id(row.ppid)} --> {_safe_node_id(row.pid)}")
    for pid in sorted(additions & by_pid.keys()):
        lines.append(f"  style {_safe_node_id(pid)} fill:#d5f5e3,stroke:#1e8449")
    recent = [row for row in rows if row.recent]
    lines.extend((
        "```",
        "",
        f"- owner_pids: `{','.join(str(pid) for pid in owner_pids) or 'none'}`",
        f"- process_count: `{len(rows)}`",
        f"- recent_count: `{len(recent)}`",
        f"- added_count: `{len(additions)}`",
    ))
    return "\n".join(lines) + "\n"


def collect_lineage_snapshot(owner_name: str, *, recent_minutes: float = 10.0) -> dict[str, object]:
    if recent_minutes < 0:
        raise ValueError("recent_minutes must be zero or greater")
    now = time.time()
    info: dict[int, dict[str, object]] = {}
    inaccessible = 0
    for process in psutil.process_iter(["pid", "ppid", "name", "cmdline", "create_time"]):
        try:
            item = dict(process.info)
            if (
                not isinstance(item.get("pid"), int)
                or not isinstance(item.get("ppid"), int)
                or not isinstance(item.get("name"), str)
                or not item["name"]
                or not isinstance(item.get("create_time"), (int, float))
                or float(item["create_time"]) <= 0
            ):
                inaccessible += 1
                continue
            info[process.pid] = item
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            inaccessible += 1

    owner_pids = {
        pid for pid, item in info.items()
        if str(item.get("name") or "").lower() == owner_name.lower()
    }
    descendants: set[int] = set(owner_pids)
    changed = True
    while changed:
        changed = False
        for pid, item in info.items():
            if pid not in descendants and int(item.get("ppid") or 0) in descendants:
                descendants.add(pid)
                changed = True

    rows: list[LineageRow] = []
    for pid in descendants:
        item = info[pid]
        created = float(item.get("create_time") or 0.0)
        depth = 0
        parent = int(item.get("ppid") or 0)
        seen = {pid}
        while parent in descendants and parent not in seen:
            seen.add(parent)
            depth += 1
            parent = int(info[parent].get("ppid") or 0)
        cmdline = [str(value) for value in (item.get("cmdline") or [])]
        identity = command_identity(cmdline)[:12] if cmdline else "unavailable"
        rows.append(LineageRow(
            pid=pid,
            ppid=int(item.get("ppid") or 0),
            name=str(item.get("name") or "unknown"),
            created_at=datetime.fromtimestamp(created, timezone.utc).isoformat(),
            depth=depth,
            identity_prefix=identity,
            recent=(now - created) <= recent_minutes * 60,
        ))

    rows.sort(key=lambda row: (row.depth, row.created_at, row.pid))
    return {
        "schema_version": "runtime-process-guard/lineage-v1",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "owner": owner_name.lower(),
        "owner_pids": sorted(owner_pids),
        "read_only": True,
        "recent_minutes": recent_minutes,
        "process_count": len(rows),
        "recent_count": sum(row.recent for row in rows),
        "max_depth": max((row.depth for row in rows), default=0),
        "inaccessible_processes": inaccessible,
        "processes": [asdict(row) for row in rows],
    }
