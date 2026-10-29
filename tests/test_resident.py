from __future__ import annotations

import argparse
import json
import sys
from types import SimpleNamespace

import pytest

from runtime_process_guard import inventory
from runtime_process_guard import resident as resident


def report():
    value = inventory.build_report([
        dict(pid=1, ppid=0, name="ChatGPT", created_at=1,
             rss_bytes=10, cpu_seconds=0),
        dict(pid=2, ppid=1, name="node", created_at=2,
             rss_bytes=20, cpu_seconds=0),
    ], [1], [], now=100)
    value.update(collection_complete=True, owner_found=True)
    return value


@pytest.fixture
def setup(monkeypatch, tmp_path):
    args = argparse.Namespace(owner="ChatGPT", state=tmp_path / "inventory.json",
                              bindings=None, threads_db=None)
    calls = []
    value = report()
    monkeypatch.setattr(resident.time, "time", lambda: 101)
    monkeypatch.setattr(inventory, "snapshot", lambda args: calls.append(args) or value)
    monkeypatch.setattr(resident, "collect_pressure", lambda: dict(
        status="normal", memory_pressure_level=1, swap_used_bytes=0))
    return args, value, calls


def test_one_observation_preserves_schema_and_unique_resource_totals(setup):
    args, original, calls = setup
    result = resident.snapshot(args)
    assert len(calls) == 1
    assert result["inventory"] == original
    assert result["inventory"]["totals"]["rss_bytes"] == 30
    assert result["next_interval_seconds"] == 30
    assert result["automatic_cleanup_supported"] is (sys.platform == "darwin")
    assert result["managed_connections_registered"] == 0
    assert {p["reason"] for p in result["assessment"]} == {"unknown-usage"}
    assert all(p["status"] == "protected" for p in result["assessment"])


@pytest.mark.parametrize("usage,attribution,name,reason", [
    ("unknown", "attributed", "node", "unknown-usage"),
    ("idle", "attributed", "node", "unowned"),
    ("active", "attributed", "node", "active"),
    ("idle", "shared", "node", "shared"),
    ("idle", "attributed", "Discord Helper", "protected-process"),
    ("idle", "attributed", "Orca", "protected-process"),
])
def test_usage_and_names_never_grant_stop_rights(usage, attribution, name, reason):
    value = report()
    value["processes"][0].update(usage_state=usage, attribution=attribution,
                                  name=name, cpu_percent=0, lease_expired=True)
    assessment = resident.assess(value)[0]
    assert assessment == dict(pid=1, created_at=1, status="protected", reason=reason)


@pytest.mark.parametrize("field,value", [
    ("sample_epoch", 102), ("sample_epoch", 1),
    ("sample_epoch", float("nan")), ("schema_version", "bad"),
])
def test_invalid_or_future_snapshot_is_not_exposed(setup, field, value):
    args, source, _ = setup
    source[field] = value
    result = resident.snapshot(args)
    assert result["inventory"] is None
    assert result["assessment"] == []
    assert result["overall"] == "unknown"
    assert result["next_interval_seconds"] == 60


def test_incomplete_observation_is_unknown_without_enforcement(setup):
    args, source, _ = setup
    source["collection_complete"] = False
    result = resident.snapshot(args)
    assert result["error_code"] == "incomplete-observation"
    assert result["next_interval_seconds"] == 60
    assert result["automatic_cleanup_supported"] is (sys.platform == "darwin")
    assert result["managed_connections_registered"] == 0


def test_existing_future_state_is_preserved_and_not_used(setup):
    args, _, calls = setup
    args.state.write_text(json.dumps(dict(schema_version=inventory.SCHEMA, sample_epoch=200)))
    before = args.state.read_bytes()
    result = resident.snapshot(args)
    assert result["inventory"] is None
    assert calls == []
    assert args.state.read_bytes() == before


def test_snapshot_error_does_not_return_old_good_cache(setup, monkeypatch):
    args, source, _ = setup
    args.state.write_text(json.dumps(source))
    def fail(args):
        raise OSError("private path and secret must not appear")
    monkeypatch.setattr(inventory, "snapshot", fail)
    result = resident.snapshot(args)
    assert result["inventory"] is None
    assert "private path" not in json.dumps(result)


@pytest.mark.parametrize("owner,state", [("Orca", None), ("ChatGPT", "relative.json")])
def test_invalid_input_never_calls_collector(setup, owner, state):
    args, _, calls = setup
    args.owner = owner
    if state:
        args.state = type(args.state)(state)
    assert resident.snapshot(args)["overall"] == "unknown"
    assert not calls


@pytest.mark.parametrize("status,interval", [("pressured", 15), ("unknown", 60)])
def test_pressure_changes_interval(setup, monkeypatch, status, interval):
    args, _, _ = setup
    monkeypatch.setattr(resident, "collect_pressure", lambda: dict(
        status=status, memory_pressure_level=None, swap_used_bytes=0))
    assert resident.snapshot(args)["next_interval_seconds"] == interval


def test_pressure_subprocess_is_bounded_and_no_argv_collected(monkeypatch):
    monkeypatch.setattr(resident.sys, "platform", "darwin")
    monkeypatch.setattr(resident.psutil, "swap_memory", lambda: SimpleNamespace(used=100))
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="2\n")
    monkeypatch.setattr(resident.subprocess, "run", run)
    assert resident.collect_pressure() == dict(
        status="pressured", memory_pressure_level=2, swap_used_bytes=100)
    assert calls[0][0] == ["/usr/sbin/sysctl", "-n", "kern.memorystatus_vm_pressure_level"]
    assert calls[0][1]["timeout"] == 1


def test_other_platform_does_not_execute_sysctl(monkeypatch):
    monkeypatch.setattr(resident.sys, "platform", "win32")
    monkeypatch.setattr(resident.psutil, "swap_memory", lambda: SimpleNamespace(used=0))
    monkeypatch.setattr(resident.subprocess, "run", lambda *a, **k: pytest.fail("sysctl"))
    assert resident.collect_pressure()["status"] == "unknown"


@pytest.mark.parametrize("change", ["reused-pid", "future-generation"])
def test_process_identity_validation_fails_closed(setup, change):
    args, source, _ = setup
    if change == "reused-pid":
        source["processes"][1]["pid"] = source["processes"][0]["pid"]
    else:
        source["processes"][1]["created_at"] = 102
    assert resident.snapshot(args)["inventory"] is None


def test_pressure_timeout_stays_unknown(monkeypatch):
    monkeypatch.setattr(resident.sys, "platform", "darwin")
    monkeypatch.setattr(resident.psutil, "swap_memory", lambda: SimpleNamespace(used=0))
    def timeout(*args, **kwargs):
        raise resident.subprocess.TimeoutExpired("sysctl", 1)
    monkeypatch.setattr(resident.subprocess, "run", timeout)
    assert resident.collect_pressure()["status"] == "unknown"


def test_json_cli_failure_reports_unavailable_not_old_state(setup, monkeypatch, capsys):
    args, _, _ = setup
    def fail(args):
        raise OSError("private secret")
    monkeypatch.setattr(inventory, "snapshot", fail)
    exit_code = resident.main(["snapshot", "--owner", "ChatGPT", "--state", str(args.state), "--json"])
    result = json.loads(capsys.readouterr().out)
    assert exit_code == 40
    assert result["schema"] == resident.SCHEMA
    assert result["inventory"] is None
    assert result["automatic_cleanup_supported"] is (sys.platform == "darwin")
    assert result["managed_connections_registered"] == 0
