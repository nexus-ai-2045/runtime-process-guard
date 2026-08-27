import json
from pathlib import Path

import pytest

from runtime_process_guard import plugin_window_policy as policy
from runtime_process_guard.plugin_window_policy import (
    BACKUP_SUFFIX,
    build_receipt,
    enforce_headless_policy,
    reconcile_plugin_cache,
    run_cli,
)


def rel(*parts: str) -> str:
    """OS の区切り文字で相対 path を組む (CI は ubuntu と windows 両方回る)。"""
    return str(Path(*parts))


def _npx_manifest(cache: Path, *dirs: str, name: str = ".mcp.json") -> Path:
    manifest = cache.joinpath(*dirs, name)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps({"tool": {"command": "npx.cmd", "args": ["tool"]}}),
        encoding="utf-8",
    )
    return manifest


def test_enforce_headless_policy_wraps_npx_and_is_idempotent() -> None:
    source = {
        "mcpServers": {
            "browser": {"command": "npx", "args": ["browser-mcp@1.0.0"]}
        }
    }
    updated, changed, unsupported = enforce_headless_policy(source)
    second, changed_again, _ = enforce_headless_policy(updated)

    server = updated["mcpServers"]["browser"]
    assert (changed, unsupported) == (1, 0)
    assert server["command"] == "conhost.exe"
    assert server["args"][:6] == [
        "--headless",
        "cmd.exe",
        "/d",
        "/s",
        "/c",
        "npx.cmd",
    ]
    assert second == updated
    assert changed_again == 0


def test_wrapper_does_not_embed_an_absolute_interpreter_path() -> None:
    """SystemRoot が C:\\Windows でない環境で壊れないこと。"""
    updated, _, _ = enforce_headless_policy(
        {"tool": {"command": "npx", "args": ["x"]}}
    )

    assert "cmd.exe" in updated["tool"]["args"]
    assert not any(":" in arg for arg in updated["tool"]["args"])


def test_enforce_headless_policy_flags_untransformable_npx() -> None:
    """npx なのに args の形が想定外なら準拠済みに数えない。"""
    updated, changed, unsupported = enforce_headless_policy(
        {"a": {"command": "npx", "args": "not-a-list"},
         "b": {"command": "npx", "args": ["ok", 1]}}
    )

    assert (changed, unsupported) == (0, 2)
    assert updated["a"]["command"] == "npx"


def test_reconcile_supports_dot_mcp_json(tmp_path) -> None:
    manifest = _npx_manifest(tmp_path / "plugins" / "cache", "vendor", "tool")

    result = reconcile_plugin_cache(tmp_path, apply=True)
    payload = json.loads(manifest.read_text(encoding="utf-8"))

    assert result.changed == 1
    assert result.applied is True
    assert not result.invalid
    assert payload["tool"]["command"] == "conhost.exe"


def test_reconcile_does_not_treat_plugin_metadata_as_direct_mcp_config(
    tmp_path,
) -> None:
    """plugin.json は mcpServers 配下だけを対象にし、metadata を誤変換しない。"""
    manifest = tmp_path / "plugins" / "cache" / "vendor" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    original = {"launcher": {"command": "npx", "args": ["build-plugin"]}}
    manifest.write_text(json.dumps(original), encoding="utf-8")

    result = reconcile_plugin_cache(tmp_path, apply=True)

    assert result.changed == 0
    assert json.loads(manifest.read_text(encoding="utf-8")) == original


def test_reconcile_fails_closed_before_any_write(tmp_path) -> None:
    cache = tmp_path / "plugins" / "cache"
    valid = cache / "a" / "plugin.json"
    invalid = cache / "b" / ".mcp.json"
    valid.parent.mkdir(parents=True)
    invalid.parent.mkdir(parents=True)
    original = json.dumps(
        {"mcpServers": {"a": {"command": "npx", "args": ["a"]}}}
    )
    valid.write_text(original, encoding="utf-8")
    invalid.write_text("{broken", encoding="utf-8")

    result = reconcile_plugin_cache(tmp_path, apply=True)

    assert result.changed == 0
    assert result.invalid == (rel("b", ".mcp.json"),)
    assert valid.read_text(encoding="utf-8") == original


def test_reconcile_reports_unsupported_manifest_as_invalid(tmp_path) -> None:
    cache = tmp_path / "plugins" / "cache"
    manifest = cache / "a" / ".mcp.json"
    manifest.parent.mkdir(parents=True)
    original = json.dumps({"a": {"command": "npx", "args": "not-a-list"}})
    manifest.write_text(original, encoding="utf-8")

    result = reconcile_plugin_cache(tmp_path, apply=True)

    assert result.invalid == (f"unsupported:{rel('a', '.mcp.json')}",)
    assert result.already_compliant == 0
    assert manifest.read_text(encoding="utf-8") == original


def test_reconcile_reports_traversal_failure_instead_of_ok(tmp_path) -> None:
    """cache を降りられない時に scanned=0 / overall=ok を報告しない。"""
    cache = tmp_path / "plugins" / "cache"
    cache.parent.mkdir(parents=True)
    cache.write_text("not a directory", encoding="utf-8")

    result = reconcile_plugin_cache(tmp_path)

    assert result.invalid and result.invalid[0].startswith("walk:")
    assert build_receipt(result, mode="check")["overall"] == "unknown"


def test_reconcile_does_not_follow_directory_links(tmp_path) -> None:
    cache = tmp_path / "plugins" / "cache"
    target = tmp_path / "outside"
    target.mkdir()
    (target / "plugin.json").write_text(
        json.dumps({"mcpServers": {"x": {"command": "npx", "args": ["x"]}}}),
        encoding="utf-8",
    )
    cache.mkdir(parents=True)
    link = cache / "linked"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink を作れない環境")

    result = reconcile_plugin_cache(tmp_path, apply=True)

    assert result.scanned == 0
    assert json.loads((target / "plugin.json").read_text(encoding="utf-8"))[
        "mcpServers"
    ]["x"]["command"] == "npx"


def test_reconcile_defaults_to_check_only(tmp_path) -> None:
    manifest = _npx_manifest(tmp_path / "plugins" / "cache", "vendor", "tool")
    original = manifest.read_text(encoding="utf-8")

    result = reconcile_plugin_cache(tmp_path)

    assert result.applied is False
    assert result.changed == 0
    assert result.pending == (rel("vendor", "tool", ".mcp.json"),)
    assert manifest.read_text(encoding="utf-8") == original


def test_reconcile_apply_does_not_keep_a_durable_copy_by_default(tmp_path) -> None:
    """原本の複製は既定で作らない (raw command line / env を残さない)。"""
    manifest = _npx_manifest(tmp_path / "plugins" / "cache", "vendor", "tool")

    reconcile_plugin_cache(tmp_path, apply=True)

    assert not list(manifest.parent.glob("*" + BACKUP_SUFFIX))


def test_reconcile_apply_can_opt_into_backup(tmp_path) -> None:
    manifest = _npx_manifest(tmp_path / "plugins" / "cache", "vendor", "tool")
    original = manifest.read_text(encoding="utf-8")

    reconcile_plugin_cache(tmp_path, apply=True, backup=True)
    backup = manifest.with_name(manifest.name + BACKUP_SUFFIX)

    assert backup.read_text(encoding="utf-8") == original


def test_reconcile_apply_does_not_clobber_existing_backup(tmp_path) -> None:
    manifest = _npx_manifest(tmp_path / "plugins" / "cache", "vendor", "tool")
    backup = manifest.with_name(manifest.name + BACKUP_SUFFIX)
    backup.write_text("first-original", encoding="utf-8")

    reconcile_plugin_cache(tmp_path, apply=True, backup=True)

    assert backup.read_text(encoding="utf-8") == "first-original"


def test_reconcile_reports_partial_write_when_a_later_write_fails(
    tmp_path, monkeypatch
) -> None:
    cache = tmp_path / "plugins" / "cache"
    first = _npx_manifest(cache, "a")
    second = _npx_manifest(cache, "b")
    calls: list[Path] = []
    real_write = policy._write_json_atomic

    def flaky(path: Path, payload: object) -> None:
        calls.append(path)
        if len(calls) == 2:
            raise OSError("disk full")
        real_write(path, payload)

    monkeypatch.setattr(policy, "_write_json_atomic", flaky)
    result = reconcile_plugin_cache(tmp_path, apply=True)

    assert result.aborted == "partial-write"
    assert result.applied is False
    assert result.changed == 1
    assert json.loads(first.read_text(encoding="utf-8"))["tool"]["command"] == (
        "conhost.exe"
    )
    assert json.loads(second.read_text(encoding="utf-8"))["tool"]["command"] == (
        "npx.cmd"
    )
    receipt = build_receipt(result, mode="apply")
    assert receipt["overall"] == "unknown"
    assert receipt["next_action"] == "inspect-partial-plugin-cache-manually"


def test_reconcile_does_not_overwrite_external_change_after_partial_write(
    tmp_path, monkeypatch
) -> None:
    """途中失敗後に自動rollbackせず、第三者更新をそのまま保持する。"""
    cache = tmp_path / "plugins" / "cache"
    first = _npx_manifest(cache, "a")
    _npx_manifest(cache, "b")
    external = b'{"tool": {"command": "node", "args": ["new"]}}\n'
    calls: list[Path] = []
    real_write = policy._write_json_atomic

    def racing_failure(path: Path, payload: object) -> None:
        calls.append(path)
        if len(calls) == 2:
            first.write_bytes(external)
            raise OSError("disk full")
        real_write(path, payload)

    monkeypatch.setattr(policy, "_write_json_atomic", racing_failure)
    result = reconcile_plugin_cache(tmp_path, apply=True)

    assert result.aborted == "partial-write"
    assert result.changed == 1
    assert first.read_bytes() == external
    receipt = build_receipt(result, mode="apply")
    assert receipt["overall"] == "unknown"
    assert receipt["next_action"] == "inspect-partial-plugin-cache-manually"


def test_reconcile_defers_when_a_manifest_changes_after_the_scan(
    tmp_path, monkeypatch
) -> None:
    """読み取り後に plugin 更新が入ったら相手の変更を上書きしない。"""
    cache = tmp_path / "plugins" / "cache"
    first = _npx_manifest(cache, "a")
    second = _npx_manifest(cache, "b")
    real_write = policy._write_json_atomic
    intruded: list[bool] = []

    def racing(path: Path, payload: object) -> None:
        real_write(path, payload)
        if not intruded:
            intruded.append(True)
            second.write_text('{"tool": {"command": "node"}}', encoding="utf-8")

    monkeypatch.setattr(policy, "_write_json_atomic", racing)
    result = reconcile_plugin_cache(tmp_path, apply=True)

    assert result.aborted == "partial-write"
    assert result.changed == 1
    assert json.loads(first.read_text(encoding="utf-8"))["tool"]["command"] == (
        "conhost.exe"
    )
    assert json.loads(second.read_text(encoding="utf-8"))["tool"]["command"] == "node"
    receipt = build_receipt(result, mode="apply")
    assert receipt["overall"] == "unknown"
    assert receipt["next_action"] == "inspect-partial-plugin-cache-manually"


def test_build_receipt_keeps_v1_keys(tmp_path) -> None:
    """稼働中 receipt の消費側を壊さないため v1 の 8 key を維持する。"""
    _npx_manifest(tmp_path / "plugins" / "cache", "vendor", "tool")
    result = reconcile_plugin_cache(tmp_path)

    receipt = build_receipt(result, mode="check")

    assert {
        "schema_version",
        "observed_at",
        "overall",
        "scanned",
        "changed",
        "already_compliant",
        "invalid_count",
        "next_action",
    } <= set(receipt)
    assert receipt["schema_version"] == SCHEMA
    assert receipt["mode"] == "check"
    assert receipt["pending"] == 1
    assert receipt["aborted"] == "none"
    assert receipt["next_action"] == "apply-headless-policy"
    assert receipt["coverage"] == {
        "plugin_window_policy": "evaluated",
        "process_lifecycle": "not-evaluated",
    }


SCHEMA = "runtime-process-guard/plugin-window-policy-v1"


def test_run_cli_check_mode_defers_and_writes_receipt(tmp_path) -> None:
    manifest = _npx_manifest(tmp_path / "plugins" / "cache", "vendor", "tool")
    original = manifest.read_text(encoding="utf-8")
    receipt_path = tmp_path / "out" / "receipt.json"

    code = run_cli(
        ["--codex-home", str(tmp_path), "--receipt", str(receipt_path)]
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

    assert code == 20
    assert receipt["mode"] == "check"
    assert receipt["changed"] == 0
    assert manifest.read_text(encoding="utf-8") == original


def test_run_cli_apply_allows(tmp_path) -> None:
    manifest = _npx_manifest(tmp_path / "plugins" / "cache", "vendor", "tool")
    receipt_path = tmp_path / "out" / "receipt.json"

    code = run_cli(
        ["--codex-home", str(tmp_path), "--receipt", str(receipt_path), "--apply"]
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

    assert code == 0
    assert receipt["mode"] == "apply"
    assert receipt["changed"] == 1
    assert receipt["coverage"]["process_lifecycle"] == "not-evaluated"
    assert json.loads(manifest.read_text(encoding="utf-8"))["tool"]["command"] == (
        "conhost.exe"
    )


def test_run_cli_reports_unknown_for_invalid_manifest(tmp_path) -> None:
    cache = tmp_path / "plugins" / "cache"
    broken = cache / "b" / ".mcp.json"
    broken.parent.mkdir(parents=True)
    broken.write_text("{broken", encoding="utf-8")
    receipt_path = tmp_path / "out" / "receipt.json"

    code = run_cli(
        ["--codex-home", str(tmp_path), "--receipt", str(receipt_path), "--apply"]
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

    assert code == 40
    assert receipt["overall"] == "unknown"
    assert receipt["next_action"] == "repair-invalid-plugin-manifest"
    assert receipt["coverage"]["process_lifecycle"] == "not-evaluated"


def test_run_cli_receipt_has_no_manifest_paths(tmp_path) -> None:
    """privacy: receipt へ plugin の path や command line を残さない。"""
    _npx_manifest(tmp_path / "plugins" / "cache", "vendor", "tool")
    receipt_path = tmp_path / "out" / "receipt.json"

    run_cli(["--codex-home", str(tmp_path), "--receipt", str(receipt_path)])
    raw = receipt_path.read_text(encoding="utf-8")

    assert "vendor" not in raw
    assert "npx" not in raw
    assert str(tmp_path) not in raw
