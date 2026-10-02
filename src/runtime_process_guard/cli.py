"""Command-line entry point. Preflight never launches or stops a process."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import psutil

from .admission import Budget, evaluate
from .collector import observe
from .feedback import evaluate_feedback
from .lineage import (
    LineageRow,
    collect_lineage_snapshot,
    compare_lineage_reports,
    render_mermaid,
)
from .shadow import collect_shadow_snapshot
from .guarded_stdio import GuardMode, run_guarded_stdio, validate_guard_options

EXIT_CODES = {"allow": 0, "reuse": 10, "defer": 20, "deny": 30, "unknown": 40}


def _owner_creation_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "owner creation time must be timezone-aware ISO 8601"
        ) from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="runtime-process-guard")
    subparsers = parser.add_subparsers(dest="action", required=True)
    probe = subparsers.add_parser("probe-stdio-owner")
    probe.add_argument(
        "--mcp", action="store_true", help="serve read-only stdio probe as MCP"
    )
    preflight = subparsers.add_parser("preflight")
    preflight.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    preflight.add_argument(
        "--reuse-policy",
        choices=("singleton", "dedicated-stdio"),
        default="singleton",
    )
    preflight.add_argument("--min-available-memory-mb", type=int, default=2048)
    preflight.add_argument("--max-cpu-percent", type=float, default=90.0)
    preflight.add_argument("command", nargs=argparse.REMAINDER)
    shadow = subparsers.add_parser("shadow-snapshot")
    shadow.add_argument("--owner", required=True, help="ancestor executable name")
    shadow.add_argument("--process-name", help="optional executable-name filter")
    shadow.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    shadow.add_argument("--report-path", type=Path)
    feedback = subparsers.add_parser("feedback-cycle")
    feedback.add_argument("--owner", required=True, help="ancestor executable name")
    feedback.add_argument("--process-name", help="optional executable-name filter")
    feedback.add_argument("--state-path", type=Path, required=True)
    feedback.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    lineage = subparsers.add_parser("lineage-snapshot")
    lineage.add_argument("--owner", required=True, help="root executable name")
    lineage.add_argument("--recent-minutes", type=float, default=10.0)
    lineage.add_argument("--report-path", type=Path, required=True)
    lineage.add_argument("--mermaid-path", type=Path, required=True)
    lineage.add_argument("--previous-report", type=Path)
    guarded = subparsers.add_parser("guarded-stdio")
    guarded.add_argument("--mode", choices=("shadow", "enforce"), default="shadow")
    guarded.add_argument("--idle-seconds", type=float, default=600.0)
    guarded.add_argument("--grace-seconds", type=float, default=30.0)
    guarded.add_argument("--lease-state", type=Path, required=True)
    guarded.add_argument("--lease-ttl-seconds", type=float, default=30.0)
    guarded.add_argument("--min-available-memory-mb", type=int, default=2048)
    guarded.add_argument("--max-cpu-percent", type=float, default=90.0)
    guarded.add_argument("--max-server-instances", type=int)
    guarded.add_argument("--max-total-instances", type=int)
    guarded.add_argument("--client-owner-pid", type=int)
    guarded.add_argument("--client-owner-created-at", type=_owner_creation_time)
    guarded.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def _write_report(path: Path, report: dict[str, object]) -> None:
    _write_text_atomic(
        path,
        json.dumps(report, ensure_ascii=False, sort_keys=True) + "\n",
    )


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _feedback_lock_is_stale(lock_path: Path) -> bool:
    try:
        payload = json.loads(lock_path.read_text(encoding="ascii"))
        pid = int(payload["pid"])
        created_at = float(payload["process_created_at"])
        process = psutil.Process(pid)
        return abs(process.create_time() - created_at) > 0.001
    except psutil.NoSuchProcess:
        return True
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return False
    except psutil.AccessDenied:
        return False


def _try_acquire_feedback_lock(state_path: Path) -> tuple[int, Path] | None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = state_path.with_name(f"{state_path.name}.lock")
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None
    try:
        payload = {
            "pid": os.getpid(),
            "process_created_at": psutil.Process(os.getpid()).create_time(),
        }
        os.write(descriptor, json.dumps(payload, sort_keys=True).encode("ascii"))
    except BaseException:
        os.close(descriptor)
        lock_path.unlink(missing_ok=True)
        raise
    return descriptor, lock_path


def _release_feedback_lock(lock: tuple[int, Path]) -> None:
    descriptor, lock_path = lock
    os.close(descriptor)
    lock_path.unlink(missing_ok=True)


def _valid_lineage_report(report: object) -> bool:
    if not isinstance(report, dict) or not isinstance(report.get("processes"), list):
        return False
    return all(
        isinstance(row, dict)
        and isinstance(row.get("pid"), int)
        and isinstance(row.get("created_at"), str)
        for row in report["processes"]
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.action == "probe-stdio-owner":
        from .stdio_probe import collect_stdio_probe, run_probe_mcp

        if args.mcp:
            return run_probe_mcp()
        report = collect_stdio_probe()
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0 if report.get("probe_status") == "collected" else EXIT_CODES["unknown"]
    if args.action == "guarded-stdio":
        command = list(args.command)
        if command and command[0] == "--":
            command.pop(0)
        try:
            options = validate_guard_options(
                mode=GuardMode(args.mode),
                idle_seconds=args.idle_seconds,
                grace_seconds=args.grace_seconds,
                command=command,
                lease_state=args.lease_state,
                lease_ttl_seconds=args.lease_ttl_seconds,
                min_available_memory_mb=args.min_available_memory_mb,
                max_cpu_percent=args.max_cpu_percent,
                max_server_instances=args.max_server_instances,
                max_total_instances=args.max_total_instances,
                client_owner_pid=args.client_owner_pid,
                client_owner_created_at=args.client_owner_created_at,
            )
        except (TypeError, ValueError) as exc:
            build_parser().error(str(exc))
        try:
            return run_guarded_stdio(options)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            # Do not expose argv, state paths or exception text in a traceback.
            print(
                json.dumps({"event": "guarded-launch-unknown", "overall": "unknown"}),
                file=sys.stderr,
            )
            return EXIT_CODES["unknown"]
    if args.action == "feedback-cycle":
        lock = _try_acquire_feedback_lock(args.state_path)
        if lock is None:
            lock_path = args.state_path.with_name(f"{args.state_path.name}.lock")
            report = {
                "overall": "unknown",
                "state_changed": False,
                "next_action": (
                    "repair-stale-feedback-lock"
                    if _feedback_lock_is_stale(lock_path)
                    else "retry-after-feedback-lock-clears"
                ),
            }
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
            return EXIT_CODES["unknown"]
        try:
            previous_state = None
            if args.state_path.exists():
                try:
                    previous_state = json.loads(
                        args.state_path.read_text(encoding="utf-8")
                    )
                except (OSError, json.JSONDecodeError):
                    report = {
                        "overall": "unknown",
                        "state_changed": False,
                        "next_action": "repair-feedback-state",
                    }
                    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
                    return EXIT_CODES["unknown"]
            snapshot = collect_shadow_snapshot(
                args.owner, process_name=args.process_name
            )
            feedback = evaluate_feedback(snapshot, previous_state)
            state = {
                "schema_version": "runtime-process-guard/feedback-v1",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "snapshot": snapshot,
                **feedback,
            }
            if feedback["persist_state"]:
                _write_report(args.state_path, state)
        finally:
            _release_feedback_lock(lock)
        if args.json:
            print(json.dumps(state, ensure_ascii=False, sort_keys=True))
        else:
            print(
                "\n".join(
                    (
                        "action: feedback-cycle",
                        f"target: {snapshot['owner']}",
                        "process_changed: false",
                        f"state_changed: {str(feedback['persist_state']).lower()}",
                        f"verified: {str(feedback['trend'] != 'unknown').lower()}",
                        f"phase: {feedback['phase']}",
                        f"trend: {feedback['trend']}",
                        f"process_delta: {feedback['process_delta']}",
                        f"generation_delta: {feedback['generation_delta']}",
                        f"next_action: {feedback['next_action']}",
                    )
                )
            )
        return (
            EXIT_CODES["unknown"]
            if not feedback["persist_state"]
            or feedback["trend"] == "unknown"
            and feedback["phase"] != "baseline"
            else 0
        )
    if args.action == "lineage-snapshot":
        previous = None
        if args.previous_report:
            try:
                if not args.previous_report.exists():
                    raise FileNotFoundError(args.previous_report)
                previous = json.loads(args.previous_report.read_text(encoding="utf-8"))
                if not _valid_lineage_report(previous):
                    raise ValueError("previous lineage report has an invalid schema")
            except (OSError, json.JSONDecodeError, ValueError, TypeError):
                print(
                    json.dumps(
                        {
                            "overall": "unknown",
                            "state_changed": False,
                            "next_action": "repair-previous-lineage-report",
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
                return EXIT_CODES["unknown"]
        report = collect_lineage_snapshot(
            args.owner, recent_minutes=args.recent_minutes
        )
        report["diff"] = compare_lineage_reports(report, previous)
        rows = [LineageRow(**row) for row in report["processes"]]
        mermaid = render_mermaid(
            rows, report["owner_pids"], added_pids=set(report["diff"]["added_pids"])
        )
        _write_report(args.report_path, report)
        _write_text_atomic(
            args.mermaid_path,
            mermaid,
        )
        complete = report.get("inaccessible_processes", 0) == 0
        print(
            json.dumps(
                {
                    "overall": "ok" if complete else "unknown",
                    "complete": complete,
                    "read_only": True,
                    "process_count": report["process_count"],
                    "recent_count": report["recent_count"],
                    "max_depth": report["max_depth"],
                    "added_count": len(report["diff"]["added_pids"]),
                    "removed_count": len(report["diff"]["removed_pids"]),
                    "report_path": str(args.report_path),
                    "mermaid_path": str(args.mermaid_path),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0 if complete else EXIT_CODES["unknown"]
    if args.action == "shadow-snapshot":
        report = collect_shadow_snapshot(args.owner, process_name=args.process_name)
        complete = (
            report.get("observation_complete") is True
            and report.get("inaccessible_processes", 0) == 0
        )
        report["overall"] = "ok" if complete else "unknown"
        report["complete"] = complete
        report["next_action"] = (
            "human-review-generation-pressure" if complete else "repair-observation"
        )
        if args.report_path:
            _write_report(args.report_path, report)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        else:
            print(
                "\n".join(
                    (
                        "action: shadow-snapshot",
                        f"target: {report['owner']}",
                        "dry_run: true",
                        "changed: false",
                        f"verified: {str(complete).lower()}",
                        f"process_count: {report['process_count']}",
                        f"generation_count_lower_bound: {report['generation_count_lower_bound']}",
                        f"report_path: {args.report_path or 'none'}",
                        f"next_action: {report['next_action']}",
                    )
                )
            )
        return 0 if complete else EXIT_CODES["unknown"]

    command = list(args.command)
    if command and command[0] == "--":
        command.pop(0)
    if not command:
        build_parser().error("preflight requires a command after --")

    budget = Budget(
        min_available_memory_mb=args.min_available_memory_mb,
        max_cpu_percent=args.max_cpu_percent,
    )
    result = evaluate(observe(command, reuse_policy=args.reuse_policy), budget)
    report = {
        "schema_version": "runtime-process-guard/preflight-v1",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform.system().lower(),
        "read_only": True,
        **asdict(result),
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        next_action = {
            "allow": "caller-may-launch",
            "reuse": "reuse-existing-process",
            "defer": "retry-after-resource-pressure-drops",
            "deny": "do-not-launch",
            "unknown": "repair-observation-before-launch",
        }[result.decision]
        lines = (
            "action: preflight",
            f"target: {result.executable}",
            "dry_run: true",
            "changed: false",
            "verified: true",
            f"decision: {result.decision}",
            f"identity: {result.identity_prefix}",
            f"existing_count: {result.existing_count}",
            f"next_action: {next_action}",
        )
        print("\n".join(lines))
    return EXIT_CODES[result.decision]


if __name__ == "__main__":
    raise SystemExit(main())
