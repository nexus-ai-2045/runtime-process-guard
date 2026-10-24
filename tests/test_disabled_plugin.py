import json
import os
import subprocess
from pathlib import Path

import psutil
import pytest

from runtime_process_guard import disabled_plugin as subject

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX operator path")

@pytest.fixture
def inputs(tmp_path):
    root = tmp_path.resolve() / "market" / "example" / "1"
    (root / ".codex-plugin").mkdir(parents=True)
    (root / ".codex-plugin/plugin.json").write_text(
        '{"name":"example","mcpServers":"./.mcp.json"}')
    executable = Path("/bin/sleep").resolve()
    (root / ".mcp.json").write_text(json.dumps({"mcpServers": {
        "api": {"command": str(executable), "args": ["60"], "cwd": "."}
    }}))
    config = tmp_path.resolve() / "config.toml"
    config.write_text('[plugins."example@market".mcp_servers.api]\nenabled = false\n')
    return subject.Inputs(config, root, "example@market", "api", executable, os.getpid(),
                          psutil.Process().create_time(), 2)


def test_plan_is_read_only_and_private(inputs):
    plan = subject.collect_plan(inputs)
    text = json.dumps(plan)
    assert plan["targets"] == []
    assert str(inputs.plugin_root) not in text
    assert str(inputs.config) not in text
    assert "/bin/sleep" not in text
    assert plan["configuration_scope"] == "operator-selected-saved-settings"


@pytest.mark.parametrize("value", ["true", '"false"'])
def test_disabled_requires_boolean_false(inputs, value):
    inputs.config.write_text(
        '[plugins."example@market".mcp_servers.api]\nenabled = ' + value + '\n'
    )
    with pytest.raises(subject.ReconcileError, match="target-not-disabled"):
        subject.collect_plan(inputs)


def test_old_marketplace_is_not_an_alias(inputs):
    inputs.config.write_text('[plugins."example@old-market"]\nenabled = false\n')
    with pytest.raises(subject.ReconcileError, match="target-not-disabled"):
        subject.collect_plan(inputs)


def test_ambiguous_manifest_rejected(inputs):
    (inputs.plugin_root / ".mcp.json").write_text(
        '{"mcpServers":{"api":{"command":"sleep","command":"node"}}}'
    )
    with pytest.raises(subject.ReconcileError, match="invalid-manifest"):
        subject.collect_plan(inputs)


@pytest.mark.parametrize("different_env", [False, True])
def test_same_startup_binding_in_another_server_is_rejected(inputs, different_env):
    p = inputs.plugin_root / ".mcp.json"
    value = json.loads(p.read_text())
    sibling = dict(value["mcpServers"]["api"])
    if different_env:
        sibling["env"] = {"ACCOUNT": "other"}
    value["mcpServers"]["enabled-sibling"] = sibling
    p.write_text(json.dumps(value))
    with pytest.raises(subject.ReconcileError, match="ambiguous-server-binding"):
        subject.collect_plan(inputs)


def test_metadata_must_bind_the_manifest_actually_inspected(inputs):
    p = inputs.plugin_root / ".codex-plugin/plugin.json"
    p.write_text('{"name":"example","mcpServers":"./other.json"}')
    with pytest.raises(subject.ReconcileError, match="invalid-manifest"):
        subject.collect_plan(inputs)


def test_manifest_cwd_cannot_escape_plugin(inputs):
    p = inputs.plugin_root / ".mcp.json"
    value = json.loads(p.read_text())
    value["mcpServers"]["api"]["cwd"] = "../../.."
    p.write_text(json.dumps(value))
    with pytest.raises(subject.ReconcileError, match="invalid-server"):
        subject.collect_plan(inputs)


def test_owner_generation_mismatch(inputs):
    from dataclasses import replace

    with pytest.raises(subject.ReconcileError, match="owner-changed"):
        subject.collect_plan(replace(inputs, owner_created=inputs.owner_created - 1))


def test_saved_config_change_rejected_before_signal(inputs, monkeypatch):
    plan = subject.collect_plan(inputs)
    inputs.config.write_text(inputs.config.read_text() + "\n")
    calls = []
    monkeypatch.setattr(subject, "_terminate", lambda *a: calls.append(a))
    with pytest.raises(subject.ReconcileError, match="plan-changed"):
        subject.apply_plan(inputs, plan, idle_confirmed=True)
    assert calls == []


def test_idle_acknowledgement_is_explicit(inputs):
    with pytest.raises(subject.ReconcileError, match="operator-confirmation-required"):
        subject.apply_plan(inputs, subject.collect_plan(inputs), idle_confirmed=False)


def test_symlink_config_rejected(inputs, tmp_path):
    from dataclasses import replace

    alias = tmp_path / "alias.toml"
    alias.symlink_to(inputs.config)
    with pytest.raises(subject.ReconcileError, match="unsafe-source"):
        subject.collect_plan(replace(inputs, config=alias))


def test_observation_denial_does_not_become_empty_plan(inputs, monkeypatch):
    monkeypatch.setattr(psutil.Process, "children",
                        lambda *a, **k: (_ for _ in ()).throw(psutil.AccessDenied()))
    with pytest.raises(psutil.AccessDenied):
        subject.collect_plan(inputs)


def test_plan_expired(inputs):
    plan = subject.collect_plan(inputs)
    plan.update(created_at=1, expires_at=2)
    with pytest.raises(subject.ReconcileError, match="plan-expired"):
        subject.apply_plan(inputs, plan, idle_confirmed=True)


def test_source_change_between_signals_reports_partial_stop(inputs, monkeypatch):
    plan = subject.collect_plan(inputs)
    first = {"pid": 901, "created": 1}
    second = {"pid": 902, "created": 2}
    plan["targets"] = [first, second]
    count = [0]

    def current(_):
        count[0] += 1
        result = json.loads(json.dumps(plan))
        if count[0] >= 3:
            result["binding"]["command_identity"] = "changed"
        return result

    calls = []
    monkeypatch.setattr(subject, "collect_plan", current)
    monkeypatch.setattr(subject, "_terminate", lambda row: calls.append(row["pid"]))
    monkeypatch.setattr(subject, "_remaining", lambda rows: 1)
    result = subject.apply_plan(inputs, plan, idle_confirmed=True, wait_seconds=0)
    assert result["overall"] == "partial-stop"
    assert calls == [901]
    assert result["unexecuted_pids"] == [902]
    assert result["selected_generations_gone"] is False


def test_pid_reuse_does_not_send_signal(monkeypatch):
    from types import SimpleNamespace

    calls = []
    monkeypatch.setattr(subject.psutil, "Process", lambda pid: SimpleNamespace(
        create_time=lambda: 2, terminate=lambda: calls.append(pid)))
    with pytest.raises(subject.ReconcileError, match="candidate-generation-changed"):
        subject._terminate({"pid": 5, "created": 1})
    assert calls == []


def fake_child(inputs, pid=900, argv=None, executable=None):
    from types import SimpleNamespace

    return SimpleNamespace(
        pid=pid, status=lambda: psutil.STATUS_RUNNING,
        cmdline=lambda: argv or [str(inputs.expected_executable), "60"],
        cwd=lambda: str(inputs.plugin_root), ppid=lambda: inputs.owner_pid,
        uids=lambda: SimpleNamespace(real=os.getuid()),
        exe=lambda: str(executable or inputs.expected_executable),
        create_time=lambda: float(pid),
    )


def test_candidate_limit_is_fail_closed(inputs, monkeypatch):
    children = [fake_child(inputs, pid) for pid in [900, 901, 902]]
    monkeypatch.setattr(psutil.Process, "children", lambda *a, **k: children)
    with pytest.raises(subject.ReconcileError, match="target-limit-exceeded"):
        subject.collect_plan(inputs)


def test_full_executable_identity_is_checked(inputs, monkeypatch):
    child = fake_child(inputs, executable=Path("/bin/echo").resolve())
    monkeypatch.setattr(psutil.Process, "children", lambda *a, **k: [child])
    with pytest.raises(subject.ReconcileError, match="candidate-executable-mismatch"):
        subject.collect_plan(inputs)


def test_relative_executable_is_resolved_from_declared_cwd(inputs, monkeypatch):
    from dataclasses import replace
    import shutil

    executable = inputs.plugin_root / "sleep-copy"
    shutil.copyfile(inputs.expected_executable, executable)
    source = replace(inputs, expected_executable=executable)
    manifest = inputs.plugin_root / ".mcp.json"
    value = json.loads(manifest.read_text())
    value["mcpServers"]["api"]["command"] = "./sleep-copy"
    manifest.write_text(json.dumps(value))
    child = fake_child(source, argv=["./sleep-copy", "60"])
    monkeypatch.setattr(psutil.Process, "children", lambda *a, **k: [child])
    assert subject.collect_plan(source)["targets"][0]["pid"] == child.pid


def test_enabled_executable_alias_cannot_become_disabled_target(inputs, monkeypatch):
    alias = inputs.plugin_root / "alternate"
    alias.symlink_to(inputs.expected_executable)
    path = inputs.plugin_root / ".mcp.json"
    manifest = json.loads(path.read_text())
    manifest["mcpServers"]["enabled-alias"] = {
        "command": "./alternate", "args": ["60"], "cwd": "."}
    path.write_text(json.dumps(manifest))
    child = fake_child(inputs, argv=["./alternate", "60"])
    monkeypatch.setattr(psutil.Process, "children", lambda *a, **k: [child])
    assert subject.collect_plan(inputs)["targets"] == []


def test_secret_arguments_and_individual_paths_are_not_in_plan(inputs, monkeypatch):
    manifest = inputs.plugin_root / ".mcp.json"
    value = json.loads(manifest.read_text())
    value["mcpServers"]["api"]["args"].append("--api-key=super-secret")
    manifest.write_text(json.dumps(value))
    child = fake_child(inputs, argv=[str(inputs.expected_executable), "60", "--api-key=super-secret"])
    monkeypatch.setattr(psutil.Process, "children", lambda *a, **k: [child])
    text = json.dumps(subject.collect_plan(inputs))
    for forbidden in ["super-secret", "--api-key", str(inputs.plugin_root), "/bin/sleep"]:
        assert forbidden not in text


def test_cli_plan_digest_and_privacy(inputs, tmp_path, capsys):
    from datetime import datetime, timezone
    from runtime_process_guard import cli

    path = tmp_path.resolve() / "plan.json"
    command = ["reconcile-disabled-plugin", "--config", str(inputs.config),
               "--plugin-root", str(inputs.plugin_root), "--plugin-id", inputs.plugin_id,
               "--server-name", inputs.server_name, "--owner-pid", str(inputs.owner_pid),
               "--expected-executable", str(inputs.expected_executable),
               "--owner-created-at", datetime.fromtimestamp(
                   inputs.owner_created, timezone.utc).isoformat(),
               "--max-targets", "2", "--plan-path", str(path)]
    assert cli.main(command) == 0
    output = capsys.readouterr().out
    assert str(tmp_path) not in output
    assert path.stat().st_mode & 0o777 == 0o600
    plan = json.loads(output)
    assert cli.main(command + ["--apply", "--idle-confirmed", "--expected-plan-sha256",
                               "0" * 64]) == 40
    assert "plan-digest-mismatch" in capsys.readouterr().out
    assert cli.main(command + ["--apply", "--idle-confirmed", "--expected-plan-sha256",
                               plan["plan_sha256"]]) == 0
    assert json.loads(capsys.readouterr().out)["signal_sent_count"] == 0


@pytest.mark.skipif(os.name != "posix", reason="explicit operator path is POSIX only")
def test_owned_fixture_is_stopped_but_other_command_is_preserved(inputs):
    target = subprocess.Popen([str(inputs.expected_executable), "60"], cwd=inputs.plugin_root)
    other = subprocess.Popen([str(inputs.expected_executable), "61"], cwd=inputs.plugin_root)
    try:
        plan = subject.collect_plan(inputs)
        assert [p["pid"] for p in plan["targets"]] == [target.pid]
        result = subject.apply_plan(inputs, plan, idle_confirmed=True, wait_seconds=2)
        assert result["overall"] == "stopped"
        assert result["selected_generations_gone"] is True
        assert other.poll() is None
    finally:
        for process in (target, other):
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=3)


@pytest.mark.skipif(os.name != "posix", reason="explicit operator path is POSIX only")
def test_new_target_since_plan_is_not_silently_added(inputs):
    plan = subject.collect_plan(inputs)
    target = subprocess.Popen([str(inputs.expected_executable), "60"], cwd=inputs.plugin_root)
    try:
        with pytest.raises(subject.ReconcileError, match="plan-changed"):
            subject.apply_plan(inputs, plan, idle_confirmed=True)
        assert target.poll() is None
    finally:
        target.terminate()
        target.wait(timeout=3)
