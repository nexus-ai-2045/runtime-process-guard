"""Command-line entry point. Preflight never launches or stops a process."""

from __future__ import annotations

import argparse
import json
import platform
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .admission import Budget, evaluate
from .collector import observe
from .shadow import collect_shadow_snapshot

EXIT_CODES = {"allow": 0, "reuse": 10, "defer": 20, "deny": 30, "unknown": 40}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="runtime-process-guard")
    subparsers = parser.add_subparsers(dest="action", required=True)
    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("--json", action="store_true", help="emit machine-readable JSON")
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
    shadow.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    shadow.add_argument("--report-path", type=Path)
    return parser


def _write_report(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
    ) as handle:
        json.dump(report, handle, ensure_ascii=False, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.action == "shadow-snapshot":
        report = collect_shadow_snapshot(args.owner, process_name=args.process_name)
        if args.report_path:
            _write_report(args.report_path, report)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        else:
            print("\n".join((
                "action: shadow-snapshot",
                f"target: {report['owner']}",
                "dry_run: true",
                "changed: false",
                "verified: true",
                f"process_count: {report['process_count']}",
                f"generation_count_lower_bound: {report['generation_count_lower_bound']}",
                f"report_path: {args.report_path or 'none'}",
                "next_action: human-review-generation-pressure",
            )))
        return 0

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
