from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import PurePosixPath
from types import SimpleNamespace

import pytest

from runtime_process_guard import inventory as inv
from runtime_process_guard.cli import main as cli_main

T1 = "00000000-0000-7000-8000-000000000001"
T2 = "00000000-0000-7000-8000-000000000002"


def proc(pid, parent, name="node_repl", born=1, rss=100, cpu=1):
    return dict(pid=pid, ppid=parent, name=name, created_at=born,
                rss_bytes=rss, cpu_seconds=cpu)


def binding(pid, thread=T1, born=3, expires=100):
    return dict(pid=pid, created_at=born, thread_id=thread,
                verified_at=10, expires_at=expires, method="live-marker-and-os-fd",
                scope="execution-kernel", server_pid=20, server_created_at=2,
                evidence_sha256="a" * 64)


def tree():
    return [proc(10, 1, "ChatGPT"), proc(20, 10, born=2),
            proc(21, 20, "node", born=3), proc(22, 21, "node", born=4), proc(30, 10, born=4),
            proc(40, 10, "Codex (Renderer)", born=5), proc(99, 1, "Orca")]


@pytest.fixture
def display_identity(tmp_path, monkeypatch):
    username = "fixture-" + tmp_path.name[-5:]
    home = PurePosixPath("/Users") / username
    monkeypatch.setattr(inv.Path, "home", lambda: home)
    return username, home


def test_bindings_do_not_spread_to_siblings_and_totals_are_unique():
    r = inv.build_report(tree(), [10], [binding(21)], now=20, titles={T1: "実行環境"})
    rows = {p["pid"]: p for p in r["processes"]}
    assert rows[21]["thread_ids"] == rows[22]["thread_ids"] == [T1]
    assert rows[22]["binding_scope"] == "observed-descendant"
    assert rows[30]["attribution"] == rows[40]["attribution"] == "unknown"
    assert 99 not in rows
    assert r["totals"]["rss_bytes"] == 600
    assert sum(g["rss_bytes"] for g in r["chat_groups"]) == 600
    assert rows[21]["subtree"]["rss_bytes"] == 200
    assert rows[10]["subtree"]["rss_bytes"] == 600


def test_multiple_observations_are_shared_not_counted_per_chat():
    r = inv.build_report(tree(), [10], [binding(21), binding(21, T2)], now=20)
    groups = {g["key"]: g for g in r["chat_groups"]}
    assert groups["shared"]["rss_bytes"] == 200
    assert T1 not in groups and T2 not in groups
    assert sum(g["rss_bytes"] for g in groups.values()) == r["totals"]["rss_bytes"]


def test_marker_kernel_does_not_claim_shared_server_or_sibling():
    rows = tree() + [proc(23, 20, "node", born=4)]
    r = inv.build_report(rows, [10], [binding(21, born=3)], now=20, titles={T1: "実行環境"})
    by_pid = {p["pid"]: p for p in r["processes"]}
    assert by_pid[21]["thread_ids"] == [T1]
    assert by_pid[20]["thread_ids"] == by_pid[23]["thread_ids"] == []
    assert by_pid[20]["related_thread_ids"] == [T1]
    assert "実行環境（関連のみ・専有未確認）" in inv.render(r, tree=True)


def test_deep_tree_render_is_iterative():
    rows = [proc(i, i-1, born=i) for i in range(1, 1200)]
    r = inv.build_report(rows, [1], [], now=2000)
    assert r["totals"]["process_count"] == 1199
    assert "1199" in inv.render(r, tree=True)


@pytest.mark.parametrize("b", [binding(21, born=999), binding(21, expires=19), binding(555)])
def test_reused_expired_and_exited_bindings_are_rejected(b):
    r = inv.build_report(tree(), [10], [b], now=20)
    assert r["binding_rejections"] == 1
    assert all(p["attribution"] == "unknown" for p in r["processes"])


def test_nested_owners_cycles_and_reparenting_never_duplicate_or_loop():
    rows = [proc(1, 2, born=1), proc(2, 1, born=1), proc(3, 2, born=0.5)]
    r = inv.build_report(rows, [1, 2], [], now=20)
    assert len(r["processes"]) == 2
    assert r["totals"]["rss_bytes"] == 200


def test_cpu_uses_generation_and_actual_interval():
    first = inv.build_report(tree(), [10], [], now=20)
    rows = tree()
    rows[1]["cpu_seconds"] = 2
    rows[2]["created_at"] = 99
    r = inv.build_report(rows, [10], [], now=22, previous=first)
    by_pid = {p["pid"]: p for p in r["processes"]}
    assert by_pid[20]["cpu_percent"] == 50
    assert by_pid[21]["cpu_percent"] is None
    assert r["diff"]["added_pids"] == [21]
    assert r["diff"]["removed_pids"] == [21, 22]


def test_collector_requests_no_commands_env_files_or_blocking_cpu(monkeypatch):
    requested = []
    def processes(attrs):
        requested.extend(attrs)
        return [SimpleNamespace(info={"pid": 1, "ppid": 0, "name": "node_repl",
                    "create_time": 1, "memory_info": SimpleNamespace(rss=100),
                    "cpu_times": SimpleNamespace(user=1, system=2)})]
    monkeypatch.setattr(inv.psutil, "process_iter", processes)
    rows, inaccessible = inv.collect_processes()
    assert inaccessible == 0 and rows[0]["cpu_seconds"] == 3
    assert set(requested) == {"pid", "ppid", "name", "create_time", "memory_info", "cpu_times"}


def test_title_read_is_read_only_and_strips_terminal_controls(tmp_path):
    path = tmp_path / "threads.db"
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE threads (id TEXT, title TEXT)")
        c.execute("INSERT INTO threads VALUES (?,?)", (T1, "実行\x1b\n環境"))
    before = path.read_bytes()
    titles, ok = inv.chat_titles(path, {T1})
    assert ok and titles[T1] == "実行  環境"
    assert path.read_bytes() == before
    assert inv.chat_titles(tmp_path / "absent.db", {T1}) == ({}, False)
    assert not (tmp_path / "absent.db").exists()


def test_registered_ui_name_precedes_initial_prompt_title(tmp_path):
    path = tmp_path / "threads.db"
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE threads (id TEXT,name TEXT,title TEXT,archived INT,updated_at INT)")
        c.executemany("INSERT INTO threads VALUES (?,?,?,?,?)",
                      [(T1, "登録された名前", "長い初回依頼文", 0, 20),
                       (T2, None, "別チャット", 1, 10)])
    titles, ok = inv.chat_titles(path, {T1})
    assert ok and titles[T1] == "登録された名前"
    catalog = inv.registered_chats(path)
    assert len(catalog) == 1 and catalog[0]["title"] == "登録された名前"
    assert catalog[0]["process_mapping"] == "unverified"
    assert catalog[0]["attributed_pids"] == []


@pytest.mark.parametrize("use_name", [False, True])
@pytest.mark.parametrize("secret", ["api_key=example-sensitive-value",
                                   '{"api_key": "example-sensitive-value"}',
                                   "Authorization: Bearer example-sensitive-value"])
def test_title_sources_redact_private_text_before_saving_and_rendering(
        tmp_path, monkeypatch, display_identity, use_name, secret):
    username, home = display_identity
    path = tmp_path / "threads.db"
    raw_title = f"依頼 {home / 'project'} {secret}\x1b\n確認"
    name = raw_title if use_name else None
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE threads (id TEXT,name TEXT,title TEXT)")
        c.execute("INSERT INTO threads VALUES (?,?,?)", (T1, name, raw_title))
    before = path.read_bytes()
    titles, ok = inv.chat_titles(path, {T1})
    assert ok
    catalog = inv.registered_chats(path)
    registry = tmp_path / "bindings.json"
    registry.write_text(json.dumps({"schema_version": inv.BINDINGS, "bindings": [binding(21)]}))
    state = tmp_path / "state.json"
    monkeypatch.setattr(inv, "collect_processes", lambda: (tree(), 0))
    monkeypatch.setattr(inv.time, "time", lambda: 20)
    report = inv.snapshot(argparse.Namespace(state=state, owner="ChatGPT", bindings=registry, threads_db=path))
    output = json.dumps([titles, catalog, report]) + state.read_text(encoding="utf-8") + inv.render(report, tree=True)
    assert username not in output
    assert "example-sensitive-value" not in output
    assert "%USERPROFILE%/project" in output and "<redacted>" in output
    assert path.read_bytes() == before


def test_report_redacts_titles_for_direct_child_and_related_parent(display_identity):
    username, _ = display_identity
    home = PurePosixPath("/home") / username
    raw = f"確認 {home / 'project'} token=example-sensitive-value"
    report = inv.build_report(tree(), [10], [binding(21)], now=20, titles={T1: raw})
    output = json.dumps(report) + inv.render(report) + inv.render(report, tree=True)
    assert username not in output
    assert "example-sensitive-value" not in output
    parent = next(p for p in report["processes"] if p["pid"] == 20)
    assert parent["related_chat_titles"] == ["確認 %USERPROFILE%/project token=<redacted>"]


@pytest.mark.parametrize("view", [[], ["--tree"], ["--json"]])
def test_old_cached_titles_are_redacted_on_show_without_rewriting(
        tmp_path, monkeypatch, capsys, display_identity, view):
    username, home = display_identity
    path = tmp_path / "state.json"
    report = inv.build_report(tree(), [10], [binding(21)], now=20)
    raw = f"確認 {home / 'project'} Authorization: Basic example-sensitive-value"
    next(g for g in report["chat_groups"] if g["key"] == T1)["title"] = raw
    next(p for p in report["processes"] if p["pid"] == 20)["related_chat_titles"] = [raw]
    path.write_text(json.dumps(report))
    before = path.read_bytes()
    monkeypatch.setattr(inv, "collect_processes", lambda: pytest.fail("cached read enumerated processes"))
    assert cli_main(["inventory", "show", "--state", str(path), *view]) == 0
    output = capsys.readouterr().out
    assert username not in output
    assert "example-sensitive-value" not in output
    assert "%USERPROFILE%/project" in output and "<redacted>" in output
    assert path.read_bytes() == before


def test_safe_text_redacts_current_username_without_losing_control_or_length_guard(display_identity):
    username, _ = display_identity
    value = inv._safe_text(f"確認{username}さん\x1b\n" + "あ" * 150)
    assert username not in value
    assert value.startswith("確認<user>さん  ") and len(value) == 120


def test_catalog_shows_names_without_inventing_pid_correspondence(tmp_path, monkeypatch, capsys):
    path = tmp_path / "threads.db"
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE threads (id TEXT,title TEXT)")
        c.execute("INSERT INTO threads VALUES (?,?)", (T1, "実行環境"))
    report = inv.build_report(tree(), [10], [binding(21)], now=20)
    catalog = inv.registered_chats(path, report=report)
    assert catalog[0]["attributed_pids"] == [21, 22]
    assert catalog[0]["related_pids"] == [20]
    monkeypatch.setattr(inv, "collect_processes", lambda: pytest.fail("catalog scanned processes"))
    assert inv.main(["chats", "--threads-db", str(path)]) == 0
    assert "実行環境" in capsys.readouterr().out
    with pytest.raises(ValueError):
        inv.registered_chats(path, limit=1001)


def test_invalid_registry_cannot_claim_verified_binding():
    b = binding(20)
    b["method"] = "same-cwd-and-time"
    with pytest.raises(ValueError):
        inv.validate_bindings({"schema_version": inv.BINDINGS, "bindings": [b]})


def test_cached_show_performs_no_process_enumeration(tmp_path, monkeypatch, capsys):
    path = tmp_path / "state.json"
    path.write_text(json.dumps(inv.build_report(tree(), [10], [], now=20)))
    monkeypatch.setattr(inv, "collect_processes", lambda: pytest.fail("cached read enumerated processes"))
    assert cli_main(["inventory", "show", "--state", str(path), "--tree"]) == 0
    assert "未特定" in capsys.readouterr().out


def test_failed_snapshot_preserves_previous_state(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    path.write_text('{"schema_version":"wrong"}')
    before = path.read_bytes()
    assert inv.main(["snapshot", "--owner", "ChatGPT", "--state", str(path)]) == 40
    assert path.read_bytes() == before


def test_watch_is_bounded_and_cannot_poll_too_fast(tmp_path, monkeypatch):
    monkeypatch.setattr(inv, "snapshot", lambda args: pytest.fail("invalid watch started"))
    assert inv.main(["watch", "--owner", "ChatGPT", "--state", str(tmp_path / "s"),
                     "--interval", "0.1"]) == 40


def test_missing_live_marker_binding_does_not_write_registry(tmp_path):
    registry = tmp_path / "bindings.json"
    assert inv.main(["bind", "--marker", str(tmp_path / "absent"), "--kernel-pid", "123",
                     "--bindings", str(registry)]) == 40
    assert not registry.exists()


def test_symlink_output_cannot_replace_other_file(tmp_path):
    target = tmp_path / "target"
    target.write_text("keep")
    link = tmp_path / "link"
    try:
        link.symlink_to(target)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows account lacks symlink creation privilege")
        raise
    with pytest.raises(ValueError):
        inv._write(link, {"x": 1})
    assert target.read_text() == "keep"


def test_snapshot_does_not_stop_processes_and_writes_private_state(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    monkeypatch.setattr(inv, "collect_processes", lambda: (tree(), 1))
    args = argparse.Namespace(state=path, owner="ChatGPT", bindings=None, threads_db=None)
    r = inv.snapshot(args)
    assert r["read_only"] and r["collection_complete"] is False
    assert r["totals"]["process_count"] == 6
    if inv.os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600


def test_old_server_binding_schema_is_rejected():
    with pytest.raises(ValueError):
        inv.validate_bindings({"schema_version": "runtime-process-guard/chat-bindings-v1",
                               "bindings": [binding(20)]})
    b = binding(20)
    b.pop("scope")
    with pytest.raises(ValueError):
        inv.validate_bindings({"schema_version": inv.BINDINGS, "bindings": [b]})


def test_old_attribution_cache_is_rejected(tmp_path):
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"schema_version": "runtime-process-guard/inventory-v1", "sample_epoch": 20}))
    assert inv.main(["show", "--state", str(path)]) == 40


def test_even_forged_server_binding_is_not_used_for_siblings():
    r = inv.build_report(tree(), [10], [binding(20, born=2)], now=20)
    assert all(p["attribution"] == "unknown" for p in r["processes"])


@pytest.mark.skipif(inv.os.name != "posix", reason="FIFO is POSIX only")
def test_fifo_input_fails_without_blocking(tmp_path):
    path = tmp_path / "pipe"
    inv.os.mkfifo(path)
    with pytest.raises(ValueError):
        inv.read_json(path)


def test_read_limit_and_symlink_rejected(tmp_path):
    path = tmp_path / "input"
    path.write_bytes(b"a" * 20)
    with pytest.raises(ValueError):
        inv._read_bytes(path, limit=10)
    if inv.os.name == "posix":
        link = tmp_path / "link"
        link.symlink_to(path)
        with pytest.raises(OSError):
            inv.read_json(link)


def test_state_cannot_overwrite_database(tmp_path, monkeypatch):
    path = tmp_path / "threads.db"
    path.write_text("database sentinel")
    args = argparse.Namespace(state=path, owner="ChatGPT", bindings=None, threads_db=path)
    with pytest.raises(ValueError):
        inv.snapshot(args)
    assert path.read_text() == "database sentinel"


def test_inode_mismatch_rejected_even_if_path_matches(tmp_path, monkeypatch):
    marker = tmp_path / "marker"
    marker.write_text("a")
    st = marker.stat()
    kernel = SimpleNamespace(pid=123, open_files=lambda: [SimpleNamespace(path=str(marker), fd=12)])
    monkeypatch.setattr(inv.sys, "platform", "darwin")
    monkeypatch.setattr(inv.subprocess, "run", lambda *a, **kw:
                        SimpleNamespace(returncode=0, stdout=f"f12\nD0x{st.st_dev:x}\ni{st.st_ino+1}\n"))
    assert inv._kernel_holds_inode(kernel, marker, st) is False
