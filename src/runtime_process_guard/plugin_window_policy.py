"""Windows の plugin MCP を不可視コンソールで起動するよう調停する。

既定は read-only の check。`apply=True` を明示した時だけ plugin cache を書き換える。
異常時に成功を報告しないことを優先する。走査エラー、変換不能な定義、書き込み中の
失敗、読み取りから書き込みまでの間の競合は、いずれも `ok` ではなく `unknown` または
defer として返す。receipt には件数だけを残し、plugin の path や command line は保存しない。
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

SCHEMA_VERSION = "runtime-process-guard/plugin-window-policy-v1"
BACKUP_SUFFIX = ".pre-headless.bak"
MANIFEST_NAMES = {"plugin.json", ".mcp.json"}
NPX_COMMANDS = {"npx", "npx.cmd"}

# 絶対パスを埋めない。System32 は常に PATH 上にあり、CreateProcess が解決する。
# SystemRoot が C:\Windows でない環境で壊れないことと、privacy boundary の
# 「ユーザー名を含む絶対パスを保存しない」の趣旨を同時に満たす。
WRAPPER_HEAD = ("--headless", "cmd.exe", "/d", "/s", "/c", "npx.cmd")

EXIT_ALLOW = 0
EXIT_DEFER = 20
EXIT_UNKNOWN = 40

UNCHANGED = "unchanged"
CHANGED = "changed"
UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class ReconcileResult:
    scanned: int
    changed: int
    already_compliant: int
    invalid: tuple[str, ...]
    pending: tuple[str, ...] = ()
    applied: bool = False
    aborted: str | None = None


class _Conflict(Exception):
    """読み取り後に manifest が第三者に変更された。"""


def _headless_server(server: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """npx 起動の定義を conhost --headless で包む。副作用なし。

    npx なのに args の形が想定外なら UNSUPPORTED を返す。呼び出し側が
    「準拠済み」と数えないようにするため、unchanged と区別する。
    """
    command = str(server.get("command", "")).lower()
    if command not in NPX_COMMANDS:
        return server, UNCHANGED
    args = server.get("args", [])
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        return server, UNSUPPORTED
    updated = dict(server)
    updated["command"] = "conhost.exe"
    updated["args"] = [*WRAPPER_HEAD, *args]
    return updated, CHANGED


def enforce_headless_policy(
    payload: Any, *, allow_direct: bool = True
) -> tuple[Any, int, int]:
    """既知の plugin.json / .mcp.json 構造を副作用なしで変換する。

    返り値は (更新後 payload, 変換した数, 変換不能だった数)。
    """
    if not isinstance(payload, dict):
        return payload, 0, 0
    servers = payload.get("mcpServers")
    if not isinstance(servers, dict) and not allow_direct:
        return payload, 0, 0
    target = servers if isinstance(servers, dict) else payload
    transformed = dict(target)
    changed = 0
    unsupported = 0
    for name, server in target.items():
        if not isinstance(server, dict):
            continue
        transformed[name], status = _headless_server(server)
        changed += int(status == CHANGED)
        unsupported += int(status == UNSUPPORTED)
    if isinstance(servers, dict):
        updated = dict(payload)
        updated["mcpServers"] = transformed
        return updated, changed, unsupported
    return transformed, changed, unsupported


def _is_link_like(path: Path) -> bool:
    """symlink と Windows の junction / reparse point をまとめて弾く。

    junction は `Path.is_symlink()` では検出できず、`os.walk(followlinks=False)` も
    降下を止めない。stat できないものは fail-closed で link 扱いにする
    (`os.path.isjunction` は 3.12+ なので使わない。CI は 3.11 も回す)。
    """
    try:
        info = os.stat(path, follow_symlinks=False)
    except OSError:
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
        return True
    return path.is_symlink()


def _write_json_atomic(path: Path, payload: Any) -> None:
    serialized = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )
    _write_bytes_atomic(path, serialized)


def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb", dir=path.parent, delete=False
        ) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _keep_backup(path: Path) -> None:
    """`backup=True` を明示した時だけ原本を退避する。

    既定で退避しないのは、原本が raw command line や env を含み、durable な複製を
    増やすと plugin 側がその値を消した後も残ってしまうため。通常の巻き戻しは
    apply 中にメモリへ保持した原本で行う。既存 backup は最初の原本なので壊さない。
    """
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    if backup.exists():
        return
    backup.write_bytes(path.read_bytes())


def _collect_manifests(cache: Path) -> tuple[list[Path], list[str]]:
    """cache 配下の manifest を集める。走査エラーは黙って捨てず返す。"""
    candidates: list[Path] = []
    walk_errors: list[str] = []

    def on_error(error: OSError) -> None:
        target = getattr(error, "filename", None) or str(cache)
        try:
            label = str(Path(target).relative_to(cache))
        except ValueError:
            label = Path(str(target)).name
        walk_errors.append(f"walk:{label}")

    for root, directories, files in os.walk(cache, followlinks=False, onerror=on_error):
        directories[:] = [
            name for name in directories if not _is_link_like(Path(root) / name)
        ]
        for name in files:
            if name in MANIFEST_NAMES:
                candidates.append(Path(root) / name)
    candidates.sort()
    return candidates, walk_errors


def reconcile_plugin_cache(
    codex_home: Path, *, apply: bool = False, backup: bool = False
) -> ReconcileResult:
    cache = codex_home / "plugins" / "cache"
    candidates, invalid = _collect_manifests(cache)
    pending: list[tuple[Path, Any, bytes]] = []
    compliant = 0
    for path in candidates:
        try:
            raw = path.read_bytes()
            payload = json.loads(raw.decode("utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            invalid.append(str(path.relative_to(cache)))
            continue
        updated, changed, unsupported = enforce_headless_policy(
            payload, allow_direct=path.name == ".mcp.json"
        )
        if unsupported:
            # npx 起動なのに包めない定義。準拠済みに数えず fail-closed。
            invalid.append(f"unsupported:{path.relative_to(cache)}")
            continue
        if changed:
            pending.append((path, updated, raw))
        else:
            compliant += 1
    pending_names = tuple(str(path.relative_to(cache)) for path, _, _ in pending)
    if invalid:
        return ReconcileResult(
            len(candidates), 0, compliant, tuple(invalid), pending_names, False
        )
    if not apply:
        return ReconcileResult(
            len(candidates), 0, compliant, (), pending_names, False
        )
    return _apply_pending(len(candidates), compliant, pending, pending_names, backup)


def _apply_pending(
    scanned: int,
    compliant: int,
    pending: list[tuple[Path, Any, bytes]],
    pending_names: tuple[str, ...],
    backup: bool,
) -> ReconcileResult:
    """書き込み。途中失敗は自動rollbackせず partial-write として止める。

    通常の filesystem API には「内容が期待値なら置換する」という compare-and-swap が
    ない。読取り確認後の自動rollbackは、その直後に入った第三者更新を上書きし得る。
    そのため適用済みmanifestは保持し、件数だけを返して人間確認へ上げる。
    """
    written = 0
    aborted: str | None = None
    for path, payload, original in pending:
        try:
            if path.read_bytes() != original:
                raise _Conflict(str(path))
            if backup:
                _keep_backup(path)
            _write_json_atomic(path, payload)
        except _Conflict:
            aborted = "conflict"
            break
        except OSError:
            aborted = "write-error"
            break
        written += 1
    if aborted is None:
        return ReconcileResult(
            scanned, len(pending), compliant, (), pending_names, True
        )
    if written:
        aborted = "partial-write"
    return ReconcileResult(
        scanned, written, compliant, (), pending_names, False, aborted
    )


def _next_action(result: ReconcileResult) -> str:
    if result.aborted == "partial-write":
        return "inspect-partial-plugin-cache-manually"
    if result.aborted == "rollback-incomplete":
        return "restore-plugin-cache-manually"
    if result.aborted:
        return "retry-headless-policy"
    if result.invalid:
        return "repair-invalid-plugin-manifest"
    if result.pending and not result.applied:
        return "apply-headless-policy"
    return "none"


def _overall(result: ReconcileResult) -> str:
    if result.invalid or result.aborted in {
        "write-error",
        "rollback-incomplete",
        "partial-write",
    }:
        return "unknown"
    return "ok"


def build_receipt(
    result: ReconcileResult, *, mode: str, observed_at: str | None = None
) -> dict[str, object]:
    """件数だけの receipt を組む。path / command line は載せない。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "observed_at": observed_at or datetime.now(timezone.utc).isoformat(),
        "overall": _overall(result),
        "coverage": {
            "plugin_window_policy": "evaluated",
            "process_lifecycle": "not-evaluated",
        },
        "mode": mode,
        "aborted": result.aborted or "none",
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
    if result.invalid or result.aborted in {
        "write-error",
        "rollback-incomplete",
        "partial-write",
    }:
        return EXIT_UNKNOWN
    if result.aborted == "conflict":
        return EXIT_DEFER
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
        "--backup",
        action="store_true",
        help="--apply 時に原本を退避する。既定は退避しない (durable な複製を増やさない)",
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
