"""メニューバー向けの一回の観測。停止権限や常駐daemonは提供しない。"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import psutil

from . import inventory

SCHEMA = "runtime_process_guard.resident.v1"
_ALLOWED_OWNERS = {"chatgpt", "codex", "codex.exe"}


def collect_pressure() -> dict:
    """Macのpressureを読む。swap残量だけで現在の圧迫を推測しない。"""
    pressure = {"status": "unknown", "memory_pressure_level": None,
                "swap_used_bytes": None}
    try:
        swap = psutil.swap_memory().used
        if type(swap) is int and swap >= 0:
            pressure["swap_used_bytes"] = swap
    except (psutil.Error, OSError):
        pass
    if sys.platform != "darwin":
        return pressure
    try:
        result = subprocess.run(
            ["/usr/sbin/sysctl", "-n", "kern.memorystatus_vm_pressure_level"],
            capture_output=True, text=True, timeout=1, check=False,
        )
        if result.returncode != 0:
            return pressure
        level = int(result.stdout.strip())
        if level not in (1, 2, 4):
            return pressure
        pressure["memory_pressure_level"] = level
        if pressure["swap_used_bytes"] is not None:
            pressure["status"] = "normal" if level == 1 else "pressured"
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return pressure


def assess(report: dict) -> list[dict]:
    """帰属・未使用推測は所有権ではない。現在はすべて保護する。"""
    rows = []
    for process in report["processes"]:
        name = process.get("name", "").lower()
        if "discord" in name or "orca" in name:
            reason = "protected-process"
        elif process.get("attribution") == "shared":
            reason = "shared"
        elif process.get("usage_state") == "active":
            reason = "active"
        elif process.get("usage_state") != "idle":
            reason = "unknown-usage"
        else:
            # idle/低CPU/子なし/lease期限切れも停止許可を作らない。
            reason = "unowned"
        rows.append({"pid": process["pid"], "created_at": process["created_at"],
                     "status": "protected", "reason": reason})
    return rows


def _finite(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _validate_report(report: dict, now: float) -> None:
    if (report.get("schema_version") != inventory.SCHEMA
            or not _finite(report.get("sample_epoch"))
            or not 0 < report["sample_epoch"] <= now
            or now - report["sample_epoch"] > 60
            or not isinstance(report.get("processes"), list)):
        raise ValueError("invalid-or-stale-observation")
    seen = set()
    for row in report["processes"]:
        if (not isinstance(row, dict) or type(row.get("pid")) is not int
                or row["pid"] <= 0 or not _finite(row.get("created_at"))
                or not 0 < row["created_at"] <= report["sample_epoch"]
                or not isinstance(row.get("name"), str)
                or row["pid"] in seen):
            raise ValueError("invalid-process-generation")
        seen.add(row["pid"])


def _base() -> dict:
    return {"schema": SCHEMA, "schema_version": 1, "overall": "unknown",
            "inventory": None, "assessment": [],
            "pressure": {"status": "unknown", "memory_pressure_level": None,
                         "swap_used_bytes": None},
            "next_interval_seconds": 60, "automatic_cleanup_supported": sys.platform == "darwin",
            "automatic_cleanup_reason": ("managed-connection-end-only" if sys.platform == "darwin"
                                         else "active-request-tracking-unavailable"),
            # 対応能力と実接続の登録を分離。PID一覧は登録証拠にしない。
            "managed_connections_registered": 0,
            "error_code": None}


def snapshot(args: argparse.Namespace) -> dict:
    """既存inventoryを一度だけ実行し、同一観測から表示判断を導出する。

    guarded-stdioのowner終了/EOF時の所有付き回収責務は元の所有者に残る。
    この入口は既存PIDの所有後付けも、idle時自動停止も行わない。
    """
    envelope = _base()
    try:
        if args.owner.lower() not in _ALLOWED_OWNERS:
            raise ValueError("unsupported-owner")
        for path in (args.state, args.bindings, args.threads_db):
            if path is not None and not path.is_absolute():
                raise ValueError("absolute-path-required")
        previous = inventory._previous(args.state)
        if previous is not None and previous["sample_epoch"] > time.time():
            raise ValueError("future-inventory-cache")
        report = inventory.snapshot(args)
        _validate_report(report, time.time())
        envelope["inventory"] = report
        envelope["assessment"] = assess(report)
        envelope["pressure"] = collect_pressure()
        if not report.get("collection_complete") or not report.get("owner_found"):
            envelope["error_code"] = "incomplete-observation"
        elif envelope["pressure"]["status"] == "unknown":
            envelope["error_code"] = "pressure-unavailable"
        else:
            envelope["overall"] = "ok"
            envelope["next_interval_seconds"] = (
                15 if envelope["pressure"]["status"] == "pressured" else 30
            )
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error,
            psutil.Error, subprocess.TimeoutExpired):
        # 例外のパス・引数・secretや、前回の正常値を現在値として返さない。
        envelope = _base()
        envelope["error_code"] = "observation-unavailable"
    return envelope


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="runtime-process-guard resident")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("snapshot")
    command.add_argument("--owner", required=True)
    command.add_argument("--state", required=True, type=Path)
    command.add_argument("--bindings", type=Path)
    command.add_argument("--threads-db", type=Path)
    command.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    envelope = snapshot(args)
    print(json.dumps(envelope, ensure_ascii=False, allow_nan=False))
    return 0 if envelope["overall"] == "ok" else 40


if __name__ == "__main__":
    raise SystemExit(main())
