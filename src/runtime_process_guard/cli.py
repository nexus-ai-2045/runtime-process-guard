"""Command-line entry point. Preflight never launches or stops a process."""

from __future__ import annotations

import argparse
import json
import platform
from dataclasses import asdict
from datetime import datetime, timezone

from .admission import Budget, evaluate
from .collector import observe

EXIT_CODES = {"allow": 0, "reuse": 10, "defer": 20, "deny": 30, "unknown": 40}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="runtime-process-guard")
    subparsers = parser.add_subparsers(dest="action", required=True)
    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    preflight.add_argument("--min-available-memory-mb", type=int, default=2048)
    preflight.add_argument("--max-cpu-percent", type=float, default=90.0)
    preflight.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = list(args.command)
    if command and command[0] == "--":
        command.pop(0)
    if not command:
        build_parser().error("preflight requires a command after --")

    budget = Budget(
        min_available_memory_mb=args.min_available_memory_mb,
        max_cpu_percent=args.max_cpu_percent,
    )
    result = evaluate(observe(command), budget)
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
