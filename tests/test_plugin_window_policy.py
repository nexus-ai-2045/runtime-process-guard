import json

from runtime_process_guard.plugin_window_policy import (
    BACKUP_SUFFIX,
    build_receipt,
    enforce_headless_policy,
    reconcile_plugin_cache,
    run_cli,
)


def _npx_manifest(cache, name=".mcp.json"):
    manifest = cache / "vendor" / "tool" / name
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
    updated, changed = enforce_headless_policy(source)
    second, changed_again = enforce_headless_policy(updated)

    server = updated["mcpServers"]["browser"]
    assert changed == 1
    assert server["command"] == "conhost.exe"
    assert server["args"][:6] == [
        "--headless",
        r"C:\Windows\System32\cmd.exe",
        "/d",
        "/s",
        "/c",
        "npx.cmd",
    ]
    assert second == updated
    assert changed_again == 0


def test_reconcile_supports_dot_mcp_json(tmp_path) -> None:
    manifest = _npx_manifest(tmp_path / "plugins" / "cache")

    result = reconcile_plugin_cache(tmp_path, apply=True)
    payload = json.loads(manifest.read_text(encoding="utf-8"))

    assert result.changed == 1
    assert result.applied is True
    assert not result.invalid
    assert payload["tool"]["command"] == "conhost.exe"


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
    assert result.invalid == (r"b\.mcp.json",)
    assert valid.read_text(encoding="utf-8") == original


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
        return

    result = reconcile_plugin_cache(tmp_path, apply=True)

    assert result.scanned == 0
    assert json.loads((target / "plugin.json").read_text(encoding="utf-8"))[
        "mcpServers"
    ]["x"]["command"] == "npx"


def test_reconcile_defaults_to_check_only(tmp_path) -> None:
    """既定は read-only。差分があっても一切書かず pending として報告する。"""
    manifest = _npx_manifest(tmp_path / "plugins" / "cache")
    original = manifest.read_text(encoding="utf-8")

    result = reconcile_plugin_cache(tmp_path)

    assert result.applied is False
    assert result.changed == 0
    assert result.pending == (r"vendor\tool\.mcp.json",)
    assert manifest.read_text(encoding="utf-8") == original
    assert not list(manifest.parent.glob("*" + BACKUP_SUFFIX))


def test_reconcile_apply_keeps_backup_beside_original(tmp_path) -> None:
    """上書き前の原本を同じディレクトリへ退避する (report/state へは出さない)。"""
    manifest = _npx_manifest(tmp_path / "plugins" / "cache")
    original = manifest.read_text(encoding="utf-8")

    reconcile_plugin_cache(tmp_path, apply=True)
    backup = manifest.with_name(manifest.name + BACKUP_SUFFIX)

    assert backup.read_text(encoding="utf-8") == original


def test_reconcile_apply_does_not_clobber_existing_backup(tmp_path) -> None:
    """2 度目の apply で最初の原本を失わない。"""
    manifest = _npx_manifest(tmp_path / "plugins" / "cache")
    backup = manifest.with_name(manifest.name + BACKUP_SUFFIX)
    backup.write_text("first-original", encoding="utf-8")

    reconcile_plugin_cache(tmp_path, apply=True)

    assert backup.read_text(encoding="utf-8") == "first-original"


def test_reconcile_apply_can_skip_backup(tmp_path) -> None:
    manifest = _npx_manifest(tmp_path / "plugins" / "cache")

    reconcile_plugin_cache(tmp_path, apply=True, backup=False)
    backup = manifest.with_name(manifest.name + BACKUP_SUFFIX)

    assert not backup.exists()


def test_build_receipt_keeps_v1_keys(tmp_path) -> None:
    """稼働中 receipt の消費側を壊さないため v1 の 8 key を維持する。"""
    _npx_manifest(tmp_path / "plugins" / "cache")
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
    assert receipt["schema_version"] == "runtime-process-guard/plugin-window-policy-v1"
    assert receipt["mode"] == "check"
    assert receipt["pending"] == 1
    assert receipt["next_action"] == "apply-headless-policy"


def test_run_cli_check_mode_defers_and_writes_receipt(tmp_path) -> None:
    manifest = _npx_manifest(tmp_path / "plugins" / "cache")
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
    manifest = _npx_manifest(tmp_path / "plugins" / "cache")
    receipt_path = tmp_path / "out" / "receipt.json"

    code = run_cli(
        ["--codex-home", str(tmp_path), "--receipt", str(receipt_path), "--apply"]
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

    assert code == 0
    assert receipt["mode"] == "apply"
    assert receipt["changed"] == 1
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


def test_run_cli_receipt_has_no_manifest_paths(tmp_path) -> None:
    """privacy: receipt へ plugin の path や command line を残さない。"""
    _npx_manifest(tmp_path / "plugins" / "cache")
    receipt_path = tmp_path / "out" / "receipt.json"

    run_cli(["--codex-home", str(tmp_path), "--receipt", str(receipt_path)])
    raw = receipt_path.read_text(encoding="utf-8")

    assert "vendor" not in raw
    assert "npx" not in raw
    assert str(tmp_path) not in raw
