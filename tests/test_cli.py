import json
from datetime import datetime, timezone

import pytest

from runtime_process_guard import cli
from runtime_process_guard.admission import Observation


@pytest.mark.parametrize("unavailable", ["host", "rss"])
def test_shadow_cli_resource_failure_is_anonymous_unknown(
    monkeypatch, capsys, unavailable
):
    from types import SimpleNamespace

    from runtime_process_guard import shadow

    def unavailable_resource():
        raise OSError("resource observation unavailable")

    class OwnedProcess:
        info = {"name": "node", "cmdline": ["node"], "create_time": 100.0}

        def cmdline(self):
            return self.info["cmdline"]

        def create_time(self):
            return self.info["create_time"]

        def parent(self):
            return SimpleNamespace(name=lambda: "codex.exe", parent=lambda: None)

        def memory_info(self):
            if unavailable == "rss":
                return unavailable_resource()
            return SimpleNamespace(rss=1024)

    monkeypatch.setattr(shadow.psutil, "process_iter", lambda *a, **k: [OwnedProcess()])
    if unavailable == "host":
        monkeypatch.setattr(shadow.psutil, "virtual_memory", unavailable_resource)

    assert cli.main(["shadow-snapshot", "--owner", "codex.exe", "--json"]) == 40
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert report["observation_complete"] is True
    assert report["resources"]["complete"] is False
    assert report["complete"] is False
    assert report["overall"] == "unknown"
    assert report["next_action"] == "repair-observation"
    assert "resource observation unavailable" not in output.out + output.err


@pytest.mark.skipif(cli.os.name != "posix", reason="POSIX advisory lock")
def test_feedback_lock_disappearing_before_reopen_is_anonymous_unknown(
    monkeypatch, tmp_path, capsys
):
    calls = []

    def disappeared_open(*args):
        calls.append(args)
        if len(calls) == 1:
            raise FileExistsError("old lock exists")
        raise FileNotFoundError("old lock disappeared")

    monkeypatch.setattr(cli.os, "open", disappeared_open)
    assert cli.main([
        "feedback-cycle", "--owner", "codex.exe", "--state-path",
        str(tmp_path / "feedback.json"), "--json",
    ]) == 40
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert report["overall"] == "unknown"
    assert report["state_changed"] is False
    assert len(calls) == 2
    assert "old lock disappeared" not in output.out + output.err


@pytest.mark.skipif(cli.os.name != "posix", reason="POSIX advisory lock")
def test_feedback_existing_lock_directory_is_anonymous_unknown(tmp_path, capsys):
    state = tmp_path / "feedback.json"
    state.with_name("feedback.json.lock").mkdir()
    assert cli.main([
        "feedback-cycle", "--owner", "codex.exe", "--state-path", str(state), "--json",
    ]) == 40
    output = capsys.readouterr()
    assert json.loads(output.out)["overall"] == "unknown"
    assert str(tmp_path) not in output.out + output.err
    assert not state.exists()


@pytest.mark.parametrize("existing", [False, True])
def test_feedback_lock_open_permission_failure_is_anonymous_unknown(
    monkeypatch, tmp_path, capsys, existing
):
    calls = []

    def denied_open(*args):
        calls.append(args)
        if existing and len(calls) == 1:
            raise FileExistsError("lock exists")
        raise PermissionError("lock permissions unavailable")

    monkeypatch.setattr(cli.os, "open", denied_open)
    state = tmp_path / "feedback.json"
    assert cli.main([
        "feedback-cycle", "--owner", "codex.exe", "--state-path", str(state), "--json",
    ]) == 40
    output = capsys.readouterr()
    assert json.loads(output.out)["overall"] == "unknown"
    assert "lock permissions unavailable" not in output.out + output.err
    assert str(tmp_path) not in output.out + output.err
    assert not state.exists()


def test_stdio_probe_cli_does_not_upgrade_observation_to_ownership(monkeypatch, capsys):
    from runtime_process_guard import stdio_probe

    monkeypatch.setattr(
        stdio_probe,
        "collect_stdio_probe",
        lambda: {"probe_status": "collected", "ownership_status": "unknown"},
    )
    assert cli.main(["probe-stdio-owner"]) == 0
    assert json.loads(capsys.readouterr().out)["ownership_status"] == "unknown"


def test_stdio_probe_cli_unknown_is_40(monkeypatch, capsys):
    from runtime_process_guard import stdio_probe

    monkeypatch.setattr(
        stdio_probe,
        "collect_stdio_probe",
        lambda: {"probe_status": "unknown", "ownership_status": "unknown"},
    )
    assert cli.main(["probe-stdio-owner"]) == 40
    assert json.loads(capsys.readouterr().out)["probe_status"] == "unknown"


def test_guarded_cli_passes_capacity_and_exact_owner_without_launch(
    monkeypatch, tmp_path
):
    received = []
    monkeypatch.setattr(
        cli, "run_guarded_stdio", lambda options: received.append(options) or 20
    )
    result = cli.main(
        [
            "guarded-stdio",
            "--lease-state",
            str(tmp_path / "leases.json"),
            "--max-server-instances",
            "2",
            "--max-total-instances",
            "4",
            "--client-owner-pid",
            "123",
            "--client-owner-created-at",
            "2026-10-02T01:00:00Z",
            "--",
            "node",
            "server.mjs",
        ]
    )
    assert result == 20
    assert received[0].max_server_instances == 2
    assert received[0].max_total_instances == 4
    assert received[0].client_owner_pid == 123
    assert received[0].client_owner_created_at == datetime(
        2026, 10, 2, 1, tzinfo=timezone.utc
    )
    assert not (tmp_path / "leases.json").exists()


def test_guarded_cli_reports_launch_failure_without_sensitive_exception(
    monkeypatch, tmp_path, capsys
):
    def failed(_options):
        raise OSError("private-command-path secret-value")

    monkeypatch.setattr(cli, "run_guarded_stdio", failed)
    assert (
        cli.main(
            [
                "guarded-stdio",
                "--lease-state",
                str(tmp_path / "leases.json"),
                "--",
                "node",
            ]
        )
        == 40
    )
    output = capsys.readouterr()
    assert json.loads(output.err)["overall"] == "unknown"
    assert "private-command-path" not in output.err
    assert "secret-value" not in output.err


@pytest.mark.parametrize(
    "extra",
    [
        ["--max-server-instances", "2"],
        ["--max-server-instances", "0", "--max-total-instances", "4"],
        ["--max-server-instances", "5", "--max-total-instances", "4"],
        ["--client-owner-pid", "123"],
        ["--client-owner-created-at", "2026-10-02T01:00:00Z"],
        [
            "--client-owner-pid",
            "123",
            "--client-owner-created-at",
            "2026-10-02T01:00:00",
        ],
    ],
)
def test_guarded_cli_rejects_invalid_capacity_or_owner_before_launch(
    monkeypatch, tmp_path, extra
):
    monkeypatch.setattr(
        cli, "run_guarded_stdio", lambda _: pytest.fail("must not launch")
    )
    with pytest.raises(SystemExit) as failure:
        cli.main(
            [
                "guarded-stdio",
                "--lease-state",
                str(tmp_path / "leases.json"),
                *extra,
                "--",
                "node",
            ]
        )
    assert failure.value.code == 2
    assert not (tmp_path / "leases.json").exists()


def test_cli_emits_redacted_machine_readable_result(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cli,
        "observe",
        lambda command, **kwargs: Observation(
            identity="b" * 64,
            executable="node.exe",
            duplicate_pids=(),
            available_memory_mb=4096,
            cpu_percent=12.5,
        ),
    )

    exit_code = cli.main(
        [
            "preflight",
            "--json",
            "--",
            r"X:\fixture-home\person\node.exe",
            "--token=secret-value",
        ]
    )
    output = capsys.readouterr().out
    report = json.loads(output)

    assert exit_code == 0
    assert report["decision"] == "allow"
    assert report["read_only"] is True
    assert "person" not in output
    assert "secret-value" not in output


def test_cli_human_output_follows_operational_contract(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cli,
        "observe",
        lambda command, **kwargs: Observation(
            identity="c" * 64,
            executable="python.exe",
            duplicate_pids=(),
            available_memory_mb=4096,
            cpu_percent=12.5,
        ),
    )

    exit_code = cli.main(["preflight", "--", "python.exe", "worker.py"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "action: preflight" in output
    assert "dry_run: true" in output
    assert "changed: false" in output
    assert "verified: true" in output
    assert "next_action: caller-may-launch" in output


def test_lineage_cli_records_added_and_removed_counts(
    monkeypatch, tmp_path, capsys
) -> None:
    previous = tmp_path / "previous.json"
    previous.write_text(
        json.dumps({"processes": [{"pid": 9, "created_at": "old"}]}), encoding="utf-8"
    )
    report_path = tmp_path / "current.json"
    mermaid_path = tmp_path / "current.md"
    monkeypatch.setattr(
        cli,
        "collect_lineage_snapshot",
        lambda owner, recent_minutes: {
            "owner_pids": [10],
            "process_count": 1,
            "recent_count": 1,
            "max_depth": 0,
            "processes": [
                {
                    "pid": 10,
                    "ppid": 1,
                    "name": "codex.exe",
                    "created_at": "new",
                    "depth": 0,
                    "identity_prefix": "a" * 12,
                    "recent": True,
                }
            ],
        },
    )

    exit_code = cli.main(
        [
            "lineage-snapshot",
            "--owner",
            "codex.exe",
            "--previous-report",
            str(previous),
            "--report-path",
            str(report_path),
            "--mermaid-path",
            str(mermaid_path),
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["added_count"] == 1
    assert output["removed_count"] == 1
    assert (
        json.loads(report_path.read_text(encoding="utf-8"))["diff"]["stable_count"] == 0
    )
    assert "style p10" in mermaid_path.read_text(encoding="utf-8")


def test_lineage_cli_fails_closed_for_invalid_previous_report(
    monkeypatch, tmp_path, capsys
) -> None:
    previous = tmp_path / "previous.json"
    previous.write_text("{broken", encoding="utf-8")
    report_path = tmp_path / "current.json"
    mermaid_path = tmp_path / "current.md"
    report_path.write_text("preserve-json", encoding="utf-8")
    mermaid_path.write_text("preserve-mermaid", encoding="utf-8")
    monkeypatch.setattr(cli, "collect_lineage_snapshot", lambda *args, **kwargs: {})

    exit_code = cli.main(
        [
            "lineage-snapshot",
            "--owner",
            "codex.exe",
            "--previous-report",
            str(previous),
            "--report-path",
            str(report_path),
            "--mermaid-path",
            str(mermaid_path),
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 40
    assert output["overall"] == "unknown"
    assert output["state_changed"] is False
    assert output["next_action"] == "repair-previous-lineage-report"
    assert report_path.read_text(encoding="utf-8") == "preserve-json"
    assert mermaid_path.read_text(encoding="utf-8") == "preserve-mermaid"


def test_lineage_cli_fails_closed_when_previous_report_is_missing(
    monkeypatch, tmp_path, capsys
) -> None:
    missing = tmp_path / "missing.json"
    report_path = tmp_path / "current.json"
    mermaid_path = tmp_path / "current.md"
    monkeypatch.setattr(cli, "collect_lineage_snapshot", lambda *args, **kwargs: {})

    exit_code = cli.main(
        [
            "lineage-snapshot",
            "--owner",
            "codex.exe",
            "--previous-report",
            str(missing),
            "--report-path",
            str(report_path),
            "--mermaid-path",
            str(mermaid_path),
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 40
    assert output["next_action"] == "repair-previous-lineage-report"
    assert not report_path.exists()
    assert not mermaid_path.exists()


def test_lineage_cli_fails_closed_for_invalid_previous_row(
    monkeypatch, tmp_path, capsys
) -> None:
    previous = tmp_path / "previous.json"
    previous.write_text(json.dumps({"processes": [{"pid": 1}]}), encoding="utf-8")
    monkeypatch.setattr(cli, "collect_lineage_snapshot", lambda *args, **kwargs: {})

    exit_code = cli.main(
        [
            "lineage-snapshot",
            "--owner",
            "codex.exe",
            "--previous-report",
            str(previous),
            "--report-path",
            str(tmp_path / "current.json"),
            "--mermaid-path",
            str(tmp_path / "current.md"),
        ]
    )

    assert exit_code == 40
    assert json.loads(capsys.readouterr().out)["overall"] == "unknown"


def test_lineage_cli_marks_incomplete_observation_unknown(
    monkeypatch, tmp_path, capsys
) -> None:
    monkeypatch.setattr(
        cli,
        "collect_lineage_snapshot",
        lambda *args, **kwargs: {
            "owner_pids": [],
            "process_count": 0,
            "recent_count": 0,
            "max_depth": 0,
            "inaccessible_processes": 1,
            "processes": [],
        },
    )

    exit_code = cli.main(
        [
            "lineage-snapshot",
            "--owner",
            "codex.exe",
            "--report-path",
            str(tmp_path / "current.json"),
            "--mermaid-path",
            str(tmp_path / "current.md"),
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 40
    assert output["overall"] == "unknown"
    assert output["complete"] is False


def test_shadow_cli_propagates_incomplete_observation(
    monkeypatch, tmp_path, capsys
) -> None:
    monkeypatch.setattr(
        cli,
        "collect_shadow_snapshot",
        lambda *args, **kwargs: {
            "schema_version": "runtime-process-guard/shadow-v1",
            "owner": "codex.exe",
            "process_name": "any",
            "read_only": True,
            "process_count": 0,
            "generation_count_lower_bound": 0,
            "inaccessible_processes": 1,
            "generations": [],
            "observation_complete": False,
        },
    )
    report_path = tmp_path / "shadow.json"

    exit_code = cli.main(
        [
            "shadow-snapshot",
            "--owner",
            "codex.exe",
            "--report-path",
            str(report_path),
        ]
    )
    output = capsys.readouterr().out
    saved = json.loads(report_path.read_text(encoding="utf-8"))

    assert exit_code == 40
    assert "verified: false" in output
    assert "next_action: repair-observation" in output
    assert saved["overall"] == "unknown"
    assert saved["complete"] is False
    assert saved["next_action"] == "repair-observation"


def test_shadow_cli_complete_json_is_verified_and_saved_consistently(
    monkeypatch, tmp_path, capsys
) -> None:
    monkeypatch.setattr(
        cli,
        "collect_shadow_snapshot",
        lambda *args, **kwargs: {
            "schema_version": "runtime-process-guard/shadow-v1",
            "owner": "codex.exe",
            "process_name": "any",
            "read_only": True,
            "process_count": 2,
            "generation_count_lower_bound": 1,
            "inaccessible_processes": 0,
            "generations": [],
            "observation_complete": True,
            "resources": {"complete": True},
        },
    )
    report_path = tmp_path / "shadow.json"

    exit_code = cli.main(
        [
            "shadow-snapshot",
            "--owner",
            "codex.exe",
            "--report-path",
            str(report_path),
            "--json",
        ]
    )
    output = json.loads(capsys.readouterr().out)
    saved = json.loads(report_path.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert output["overall"] == saved["overall"] == "ok"
    assert output["complete"] is saved["complete"] is True
    assert (
        output["next_action"]
        == saved["next_action"]
        == "human-review-generation-pressure"
    )


def test_feedback_cycle_reports_stale_lock_without_removing_it(
    monkeypatch, tmp_path, capsys
) -> None:
    state_path = tmp_path / "feedback.json"
    lock_path = state_path.with_name(f"{state_path.name}.lock")
    lock_path.write_text(
        json.dumps({"pid": 999999, "process_created_at": 1.0}), encoding="ascii"
    )
    monkeypatch.setattr(cli, "_POSIX_FEEDBACK_LOCKS", False)
    monkeypatch.setattr(cli, "_feedback_lock_is_stale", lambda path: True)

    exit_code = cli.main(
        [
            "feedback-cycle",
            "--owner",
            "codex.exe",
            "--state-path",
            str(state_path),
            "--json",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 40
    assert output["next_action"] == "repair-stale-feedback-lock"
    assert lock_path.exists()


def test_feedback_cycle_persists_state_for_next_run(
    monkeypatch, tmp_path, capsys
) -> None:
    state_path = tmp_path / "feedback-state.json"
    monkeypatch.setattr(
        cli,
        "collect_shadow_snapshot",
        lambda owner, process_name=None: {
            "schema_version": "runtime-process-guard/shadow-v1",
            "observed_at": "2026-08-09T00:00:00+00:00",
            "owner": owner,
            "process_name": process_name or "any",
            "read_only": True,
            "process_count": 12,
            "generation_count_lower_bound": 3,
            "inaccessible_processes": 0,
            "generations": [],
        },
    )

    exit_code = cli.main(
        [
            "feedback-cycle",
            "--owner",
            "codex.exe",
            "--process-name",
            "node.exe",
            "--state-path",
            str(state_path),
            "--json",
        ]
    )
    output = json.loads(capsys.readouterr().out)
    persisted = json.loads(state_path.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert output["phase"] == "baseline"
    assert output["next_action"] == "observe-next-cycle"
    assert persisted["snapshot"]["process_count"] == 12
    assert persisted["consecutive_growth_cycles"] == 0


def test_feedback_cycle_preserves_invalid_lock_marker(tmp_path, capsys) -> None:
    state_path = tmp_path / "feedback-state.json"
    lock_path = state_path.with_name(f"{state_path.name}.lock")
    lock_path.write_text("held", encoding="utf-8")

    exit_code = cli.main(
        [
            "feedback-cycle",
            "--owner",
            "codex.exe",
            "--process-name",
            "node.exe",
            "--state-path",
            str(state_path),
            "--json",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 40
    assert output["overall"] == "unknown"
    assert output["next_action"] == "repair-invalid-feedback-lock"
    assert not state_path.exists()


def test_feedback_cycle_returns_unknown_when_observation_is_incomplete(
    monkeypatch, tmp_path, capsys
) -> None:
    state_path = tmp_path / "feedback-state.json"
    monkeypatch.setattr(
        cli,
        "collect_shadow_snapshot",
        lambda owner, process_name=None: {
            "schema_version": "runtime-process-guard/shadow-v1",
            "owner": owner,
            "process_name": process_name or "any",
            "process_count": 1,
            "generation_count_lower_bound": 1,
            "inaccessible_processes": 1,
        },
    )

    exit_code = cli.main(
        [
            "feedback-cycle",
            "--owner",
            "codex.exe",
            "--process-name",
            "node.exe",
            "--state-path",
            str(state_path),
            "--json",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 40
    assert output["trend"] == "unknown"
    assert output["next_action"] == "repair-observation"
    assert not state_path.exists()


def test_feedback_cycle_does_not_overwrite_existing_state_on_incomplete_observation(
    monkeypatch, tmp_path, capsys
) -> None:
    state_path = tmp_path / "feedback-state.json"
    original = {
        "schema_version": "runtime-process-guard/feedback-v1",
        "updated_at": "2026-09-12T00:00:00+00:00",
        "snapshot": {
            "schema_version": "runtime-process-guard/shadow-v1",
            "owner": "codex.exe",
            "process_name": "any",
            "process_count": 2,
            "generation_count_lower_bound": 1,
            "inaccessible_processes": 0,
        },
        "consecutive_growth_cycles": 1,
    }
    state_path.write_text(json.dumps(original), encoding="utf-8")
    monkeypatch.setattr(
        cli,
        "collect_shadow_snapshot",
        lambda *args, **kwargs: {
            **original["snapshot"],
            "process_count": 3,
            "inaccessible_processes": 1,
        },
    )

    exit_code = cli.main(
        [
            "feedback-cycle",
            "--owner",
            "codex.exe",
            "--state-path",
            str(state_path),
            "--json",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 40
    assert output["next_action"] == "repair-observation"
    assert json.loads(state_path.read_text(encoding="utf-8")) == original


@pytest.mark.skipif(cli.os.name != "posix", reason="POSIX advisory lock")
def test_feedback_advisory_lock_survives_owner_kill(tmp_path):
    import subprocess
    import sys

    state = tmp_path / "state.json"
    state.write_text("preserved", encoding="ascii")
    script = (
        "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[2]); "
        "from runtime_process_guard import cli; "
        "lock=cli._try_acquire_feedback_lock(Path(sys.argv[1])); "
        "print(bool(lock), flush=True); sys.stdin.read()"
    )
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            script,
            str(state),
            str(cli.Path(cli.__file__).parents[1]),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline().strip() == "True"
        inode = state.with_name("state.json.lock").stat().st_ino
        marker = state.with_name("state.json.lock").read_text()
        assert cli._try_acquire_feedback_lock(state) is None
        child.kill()
        child.wait(timeout=5)
        lock = cli._try_acquire_feedback_lock(state)
        assert lock is not None
        cli._release_feedback_lock(lock)
        assert state.with_name("state.json.lock").stat().st_ino == inode
        assert state.with_name("state.json.lock").read_text() == marker
        assert state.read_text(encoding="ascii") == "preserved"
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        child.stdin.close()
        child.stdout.close()


@pytest.mark.skipif(cli.os.name != "posix", reason="POSIX advisory lock")
def test_feedback_migrates_only_confirmed_dead_legacy_owner(monkeypatch, tmp_path):
    state = tmp_path / "state.json"
    path = state.with_name("state.json.lock")
    path.write_text(json.dumps({"pid": 123, "process_created_at": 1.0}))
    inode = path.stat().st_ino
    actual_process = cli.psutil.Process

    def process(pid):
        if pid == 123:
            raise cli.psutil.NoSuchProcess(pid)
        return actual_process(pid)

    monkeypatch.setattr(cli.psutil, "Process", process)
    lock = cli._try_acquire_feedback_lock(state)
    assert lock is not None
    cli._release_feedback_lock(lock)
    assert path.stat().st_ino == inode


@pytest.mark.skipif(cli.os.name != "posix", reason="POSIX advisory lock")
@pytest.mark.parametrize("replacement", [False, True])
def test_legacy_lock_path_changes_cannot_create_two_owners(
    monkeypatch, tmp_path, replacement
):
    state = tmp_path / "state.json"
    path = state.with_name("state.json.lock")
    path.write_text(json.dumps({"pid": 123, "process_created_at": 1.0}))

    def owner_absent(_payload):
        path.unlink()
        if replacement:
            path.write_text(json.dumps({"pid": 123, "process_created_at": 1.0}))
        return True

    monkeypatch.setattr(cli, "_legacy_feedback_owner_absent", owner_absent)
    first = cli._try_acquire_feedback_lock(state)
    try:
        assert first is None
    finally:
        if first is not None:
            cli._release_feedback_lock(first)

    monkeypatch.setattr(cli, "_legacy_feedback_owner_absent", lambda _payload: True)
    second = cli._try_acquire_feedback_lock(state)
    assert second is not None
    try:
        assert cli._try_acquire_feedback_lock(state) is None
    finally:
        cli._release_feedback_lock(second)


@pytest.mark.skipif(cli.os.name != "posix", reason="POSIX advisory lock")
@pytest.mark.parametrize("link_type", ["symlink", "hardlink"])
def test_feedback_lock_links_preserve_unrelated_target(monkeypatch, tmp_path, link_type):
    state = tmp_path / "state.json"
    path = state.with_name("state.json.lock")
    target = tmp_path / "unrelated.json"
    target.write_text(json.dumps({"pid": 123, "process_created_at": 1.0}))
    before = target.read_bytes()
    if link_type == "symlink":
        path.symlink_to(target)
    else:
        cli.os.link(target, path)
    monkeypatch.setattr(cli, "_legacy_feedback_owner_absent", lambda _payload: True)
    lock = cli._try_acquire_feedback_lock(state)
    try:
        assert lock is None
        assert target.read_bytes() == before
    finally:
        if lock is not None:
            cli._release_feedback_lock(lock)


@pytest.mark.skipif(cli.os.name != "posix", reason="POSIX advisory lock")
@pytest.mark.parametrize("content", ["", "{"])
def test_partial_feedback_marker_requests_repair_and_preserves_bytes(tmp_path, capsys, content):
    state = tmp_path / "state.json"
    path = state.with_name("state.json.lock")
    path.write_text(content)
    assert cli.main([
        "feedback-cycle", "--owner", "codex.exe", "--state-path", str(state), "--json",
    ]) == 40
    report = json.loads(capsys.readouterr().out)
    assert report["next_action"] == "repair-invalid-feedback-lock"
    assert path.read_text() == content
    assert not state.exists()


@pytest.mark.skipif(cli.os.name != "posix", reason="POSIX advisory lock")
@pytest.mark.parametrize(
    "payload",
    [
        "malformed",
        "",
        "{}",
        json.dumps({"pid": True, "process_created_at": 1.0}),
        json.dumps({"pid": 123, "process_created_at": float("nan")}),
        json.dumps({"pid": 123, "process_created_at": 10**1000}),
        json.dumps({"pid": 123, "process_created_at": 1.0}),
    ],
)
def test_feedback_legacy_invalid_or_reused_pid_is_not_taken(
    monkeypatch, tmp_path, payload
):
    from types import SimpleNamespace

    state = tmp_path / "state.json"
    state.write_text("preserved")
    path = state.with_name("state.json.lock")
    path.write_text(payload)
    monkeypatch.setattr(
        cli.psutil, "Process", lambda pid: SimpleNamespace(create_time=lambda: 2.0)
    )
    assert cli._try_acquire_feedback_lock(state) is None
    assert path.read_text() == payload
    assert state.read_text() == "preserved"


@pytest.mark.skipif(cli.os.name != "posix", reason="POSIX advisory lock")
def test_feedback_live_legacy_owner_is_not_taken(tmp_path):
    state = tmp_path / "state.json"
    payload = {
        "pid": cli.os.getpid(),
        "process_created_at": cli.psutil.Process().create_time(),
    }
    state.with_name("state.json.lock").write_text(json.dumps(payload))
    assert cli._try_acquire_feedback_lock(state) is None


@pytest.mark.skipif(cli.os.name != "posix", reason="POSIX advisory lock")
def test_feedback_advisory_contention_ignores_historical_dead_pid(
    monkeypatch, tmp_path, capsys
):
    import fcntl

    state = tmp_path / "state.json"
    path = state.with_name("state.json.lock")
    path.write_text(
        json.dumps(
            {
                "schema_version": cli._ADVISORY_LOCK_SCHEMA,
                "pid": 999999,
                "process_created_at": 1.0,
            }
        )
    )
    original = path.read_text()
    descriptor = cli.os.open(path, cli.os.O_RDWR)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def absent(pid):
        raise cli.psutil.NoSuchProcess(pid)

    monkeypatch.setattr(cli.psutil, "Process", absent)
    try:
        assert (
            cli.main(
                [
                    "feedback-cycle",
                    "--owner",
                    "codex",
                    "--state-path",
                    str(state),
                    "--json",
                ]
            )
            == 40
        )
        report = json.loads(capsys.readouterr().out)
        assert report["next_action"] == "retry-after-feedback-lock-clears"
        assert report["state_changed"] is False
        assert path.read_text() == original
    finally:
        cli.os.close(descriptor)
