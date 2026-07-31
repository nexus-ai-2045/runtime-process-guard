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
        ["preflight", "--", r"X:\fixture-home\person\node.exe", "--token=secret-value"]
    )
    output = capsys.readouterr().out
    report = json.loads(output)

    assert exit_code == 0
    assert report["decision"] == "allow"
    assert report["read_only"] is True
    assert "person" not in output
    assert "secret-value" not in output
