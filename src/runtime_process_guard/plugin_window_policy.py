"""Windows の plugin MCP を不可視コンソールで起動するよう調停する。

既定は read-only の check。`apply=True` を明示した時だけ plugin cache を書き換え、
その直前に原本を同一ディレクトリへ退避する。receipt には件数だけを残し、
plugin の path や command line は保存しない。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

SCHEMA_VERSION = "runtime-process-guard/plugin-window-policy-v1"
BACKUP_SUFFIX = ".pre-headless.bak"
MANIFEST_NAMES = {"plugin.json", ".mcp.json"}

EXIT_ALLOW = 0
EXIT_DEFER = 20
EXIT_UNKNOWN = 40


@dataclass(frozen=True)
class ReconcileResult:
    scanned: int
    changed: int
    already_compliant: int
    invalid: tuple[str, ...]
    pending: tuple[str, ...] = ()
    applied: bool = False


def _headless_server(server: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    command = str(server.get("command", "")).lower()
    if command not in {"npx", "npx.cmd"}:
        return server, False
    args = server.get("args", [])
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        return server, False
    updated = dict(server)
    updated["command"] = "conhost.exe"
    updated["args"] = [
        "--headless",
        r"C:\Windows\System32\cmd.exe",
        "/d",
        "/s",
        "/c",
        "npx.cmd",
        *args,
    ]
    return updated, True


def enforce_headless_policy(payload: Any) -> tuple[Any, int]:
    """既知の plugin.json / .mcp.json 構造を副作用なしで変換する。"""
    if not isinstance(payload, dict):
        return payload, 0
    changed = 0
    updated = dict(payload)
    servers = payload.get("mcpServers")
    if isinstance(servers, dict):
        transformed = dict(servers)
        for name, server in servers.items():
            if isinstance(server, dict):
                transformed[name], did_change = _headless_server(server)
                changed += int(did_change)
        updated["mcpServers"] = transformed
        return updated, changed

    transformed = dict(payload)
    for name, server in payload.items():
        if isinstance(server, dict):
            transformed[name], did_change = _headless_server(server)
            changed += int(did_change)
    return transformed, changed


def _write_json_atomic(path: Path, payload: Any) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _keep_backup(path: Path) -> None:
    """上書き前の原本を同じディレクトリへ退避する。

    plugin cache は git 管理外なので rollback 手段が他に無い。原本は raw command line
    を含むため、report や state へは持ち出さず、既に存在する場所に留める。
    既存 backup は最初の原本なので上書きしない。
    """
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    if backup.exists():
        return
    backup.write_bytes(path.read_bytes())


def reconcile_plugin_cache(
    codex_home: Path, *, apply: bool = False, backup: bool = True
) -> ReconcileResult:
    cache = codex_home / "plugins" / "cache"
    candidates: list[Path] = []
    for root, directories, files in os.walk(cache, followlinks=False):
        directories[:] = [
            name for name in directories if not (Path(root) / name).is_symlink()
        ]
        for name in files:
            if name in MANIFEST_NAMES:
                candidates.append(Path(root) / name)
    candidates.sort()
    pending: list[tuple[Path, Any]] = []
    invalid: list[str] = []
    compliant = 0
    for path in candidates:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            invalid.append(str(path.relative_to(cache)))
            continue
        updated, changed = enforce_headless_policy(payload)
        if changed:
            pending.append((path, updated))
        else:
            compliant += 1
    pending_names = tuple(str(path.relative_to(cache)) for path, _ in pending)
    if invalid:
        # fail-closed: 1 件でも読めない manifest があれば何も書かない。
        return ReconcileResult(
            len(candidates), 0, compliant, tuple(invalid), pending_names, False
        )
    if not apply:
        return ReconcileResult(
            len(candidates), 0, compliant, (), pending_names, False
        )
    for path, payload in pending:
        if backup:
            _keep_backup(path)
        _write_json_atomic(path, payload)
    return ReconcileResult(
        len(candidates), len(pending), compliant, (), pending_names, True
    )


def _next_action(result: ReconcileResult) -> str:
    if result.invalid:
        return "repair-invalid-plugin-manifest"
    if result.pending and not result.applied:
        return "apply-headless-policy"
    return "none"


def build_receipt(
    result: ReconcileResult, *, mode: str, observed_at: str | None = None
) -> dict[str, object]:
    """件数だけの receipt を組む。path / command line は載せない。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "observed_at": observed_at or datetime.now(timezone.utc).isoformat(),
        "overall": "unknown" if result.invalid else "ok",
        "mode": mode,
        "scanned": result.scanned,
        "changed": result.changed,
        "pending": len(result.pending),
        "already_compliant": result.already_compliant,
        "invalid_count": len(result.invalid),
        "next_action": _next_action(result),
    }


def write_receipt(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _exit_code(result: ReconcileResult) -> int:
    if result.invalid:
        return EXIT_UNKNOWN
    if result.pending and not result.applied:
        return EXIT_DEFER
    return EXIT_ALLOW


def run_cli(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="plugin cache の npx 起動を conhost --headless へ調停する"
    )
    parser.add_argument("--codex-home", type=Path, default=Path.home() / ".codex")
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="実際に書き換える。既定は check のみで書き換えない",
    )
    parser.add_argument(
        "--no-backup",
        dest="backup",
        action="store_false",
        help="--apply 時の原本退避を省略する",
    )
    args = parser.parse_args(argv)
    result = reconcile_plugin_cache(
        args.codex_home, apply=args.apply, backup=args.backup
    )
    write_receipt(
        args.receipt, build_receipt(result, mode="apply" if args.apply else "check")
    )
    return _exit_code(result)


if __name__ == "__main__":  # pragma: no cover - モジュール直実行の保険
    sys.exit(run_cli())
