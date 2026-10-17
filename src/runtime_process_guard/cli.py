"""Command-line entry point. Preflight never launches or stops a process."""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import stat
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

_POSIX_FEEDBACK_LOCKS = os.name == "posix"
_ADVISORY_LOCK_SCHEMA = "runtime-process-guard/feedback-lock-posix-v1"

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
    reconcile = subparsers.add_parser("reconcile-disabled-plugin")
    reconcile.add_argument("--config", type=Path, required=True)
    reconcile.add_argument("--plugin-root", type=Path, required=True)
    reconcile.add_argument("--plugin-id", required=True)
    reconcile.add_argument("--server-name", required=True)
    reconcile.add_argument("--expected-executable", type=Path, required=True)
    reconcile.add_argument("--owner-pid", type=int, required=True)
    reconcile.add_argument("--owner-created-at", type=_owner_creation_time, required=True)
    reconcile.add_argument("--max-targets", type=int, required=True)
    reconcile.add_argument("--plan-path", type=Path, required=True)
    reconcile.add_argument("--apply", action="store_true")
    reconcile.add_argument("--idle-confirmed", action="store_true")
    reconcile.add_argument("--expected-plan-sha256")
    reconcile.add_argument("--wait-seconds", type=float, default=5)
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
        payload = _read_feedback_lock_payload(lock_path)
        if (
            isinstance(payload, dict)
            and payload.get("schema_version") == _ADVISORY_LOCK_SCHEMA
        ):
            # The immutable marker records initialization, not the live owner.
            # OS contention must never prompt removal of the locked inode.
            return False
        pid = int(payload["pid"])
        created_at = float(payload["process_created_at"])
        process = psutil.Process(pid)
        return abs(process.create_time() - created_at) > 0.001
    except psutil.NoSuchProcess:
        return True
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        OverflowError,
        json.JSONDecodeError,
    ):
        return False
    except psutil.AccessDenied:
        return False


def _valid_feedback_lock_owner(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    pid = payload.get("pid")
    created = payload.get("process_created_at")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    if isinstance(created, bool) or not isinstance(created, (int, float)):
        return False
    try:
        return math.isfinite(created) and created > 0
    except OverflowError:
        return False


def _legacy_feedback_owner_absent(payload: dict[str, object]) -> bool:
    # PID reuse, access denial and malformed ownership remain fail closed.
    try:
        psutil.Process(payload["pid"]).create_time()
    except psutil.NoSuchProcess:
        return True
    except (
        psutil.AccessDenied,
        psutil.ZombieProcess,
        OSError,
        ValueError,
        OverflowError,
    ):
        pass
    return False


def _feedback_lock_path_matches(descriptor: int, lock_path: Path) -> bool:
    try:
        opened = os.fstat(descriptor)
        named = lock_path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISREG(opened.st_mode)
        and opened.st_nlink == 1
        and opened.st_size <= 4096
        and (opened.st_dev, opened.st_ino) == (named.st_dev, named.st_ino)
    )


def _feedback_lock_needs_repair(lock_path: Path) -> bool:
    try:
        lock_path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    payload = _read_feedback_lock_payload(lock_path)
    return (
        not _valid_feedback_lock_owner(payload)
        or payload.get("schema_version") not in (None, _ADVISORY_LOCK_SCHEMA)
    )


def _read_feedback_lock_payload(lock_path: Path) -> object:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        named = lock_path.lstat()
        if not stat.S_ISREG(named.st_mode) or named.st_nlink != 1 or named.st_size > 4096:
            return None
        descriptor = os.open(lock_path, flags)
    except OSError:
        return None
    try:
        if not _feedback_lock_path_matches(descriptor, lock_path):
            return None
        return json.loads(os.read(descriptor, 4097).decode("ascii"))
    except (OSError, ValueError, UnicodeError):
        return None
    finally:
        os.close(descriptor)


def _try_acquire_feedback_lock(state_path: Path) -> tuple[int, Path] | None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = state_path.with_name(f"{state_path.name}.lock")
    created = False
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        created = True
    except FileExistsError:
        if not _POSIX_FEEDBACK_LOCKS:
            return None
        try:
            descriptor = os.open(lock_path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
        except OSError:
            return None
    except OSError:
        return None
    acquired = False
    try:
        if _POSIX_FEEDBACK_LOCKS:
            import fcntl

            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return None
            if not _feedback_lock_path_matches(descriptor, lock_path):
                return None
            if not created:
                # Keep the same inode across migration and every future cycle.
                # A legacy owner cannot flock, so verify its absence separately.
                try:
                    payload = json.loads(os.read(descriptor, 4097).decode("ascii"))
                except (ValueError, UnicodeError):
                    return None
                if not _valid_feedback_lock_owner(payload):
                    return None
                if payload.get("schema_version") == _ADVISORY_LOCK_SCHEMA:
                    # Immutable marker: an interrupted later cycle cannot corrupt
                    # the file by truncating/replacing ownership metadata.
                    if not _feedback_lock_path_matches(descriptor, lock_path):
                        return None
                    acquired = True
                    return descriptor, lock_path
                if "schema_version" in payload or not _legacy_feedback_owner_absent(
                    payload
                ):
                    return None
                if not _feedback_lock_path_matches(descriptor, lock_path):
                    return None
        payload = {
            "pid": os.getpid(),
            "process_created_at": psutil.Process(os.getpid()).create_time(),
        }
        if _POSIX_FEEDBACK_LOCKS:
            payload["schema_version"] = _ADVISORY_LOCK_SCHEMA
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.ftruncate(descriptor, 0)
        os.write(descriptor, json.dumps(payload, sort_keys=True).encode("ascii"))
        os.fsync(descriptor)
        if _POSIX_FEEDBACK_LOCKS and not _feedback_lock_path_matches(descriptor, lock_path):
            return None
        acquired = True
        return descriptor, lock_path
    finally:
        if not acquired:
            os.close(descriptor)
            if created and not _POSIX_FEEDBACK_LOCKS:
                lock_path.unlink(missing_ok=True)


def _release_feedback_lock(lock: tuple[int, Path]) -> None:
    descriptor, lock_path = lock
    os.close(descriptor)
    if not _POSIX_FEEDBACK_LOCKS:
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
    if args.action == "reconcile-disabled-plugin":
        from .disabled_plugin import run_command

        report, code = run_command(args)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return code
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
        try:
            lock = _try_acquire_feedback_lock(args.state_path)
        except OSError:
            lock = None
        if lock is None:
            lock_path = args.state_path.with_name(f"{args.state_path.name}.lock")
            report = {
                "overall": "unknown",
                "state_changed": False,
                "next_action": (
                    "repair-invalid-feedback-lock"
                    if _feedback_lock_needs_repair(lock_path)
                    else (
                        "repair-stale-feedback-lock"
                        if _feedback_lock_is_stale(lock_path)
                        else "retry-after-feedback-lock-clears"
                    )
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
            and report.get("resources", {}).get("complete") is True
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
