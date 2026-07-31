import json

from runtime_process_guard import cli
from runtime_process_guard.admission import Observation


def test_cli_emits_redacted_machine_readable_result(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cli,
        "observe",
        lambda command: Observation(
            identity="b" * 64,
            executable="node.exe",
            duplicate_pids=(),
            available_memory_mb=4096,
            cpu_percent=12.5,
        ),
    )

    exit_code = cli.main(
        ["preflight", "--json", "--", r"X:\fixture-home\person\node.exe", "--token=secret-value"]
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
        lambda command: Observation(
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
