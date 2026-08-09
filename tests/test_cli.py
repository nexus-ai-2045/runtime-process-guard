import json

from runtime_process_guard import cli
from runtime_process_guard.admission import Observation


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


def test_feedback_cycle_reports_stale_lock_without_removing_it(
    monkeypatch, tmp_path, capsys
) -> None:
    state_path = tmp_path / "feedback.json"
    lock_path = state_path.with_name(f"{state_path.name}.lock")
    lock_path.write_text(
        json.dumps({"pid": 999999, "process_created_at": 1.0}), encoding="ascii"
    )
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


def test_feedback_cycle_fails_closed_when_state_is_locked(tmp_path, capsys) -> None:
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
    assert output["next_action"] == "retry-after-feedback-lock-clears"
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
