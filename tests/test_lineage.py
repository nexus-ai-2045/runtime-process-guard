from runtime_process_guard import lineage
from runtime_process_guard.lineage import LineageRow, compare_lineage_reports, render_mermaid


def test_render_mermaid_preserves_parent_child_edges_and_marks_recent() -> None:
    rows = [
        LineageRow(10, 1, "codex.exe", "2026-08-09T00:00:00+00:00", 0, "a" * 12, False),
        LineageRow(11, 10, "cmd.exe", "2026-08-09T00:01:00+00:00", 1, "b" * 12, True),
        LineageRow(12, 11, "node.exe", "2026-08-09T00:01:01+00:00", 2, "c" * 12, True),
    ]

    rendered = render_mermaid(rows, [10], added_pids={11})

    assert "p10 --> p11" in rendered
    assert "p11 --> p12" in rendered
    assert "cmd.exe PID 11 NEW" in rendered
    assert "recent_count: `2`" in rendered
    assert "style p11" in rendered
    assert "owner_pids: `10`" in rendered


def test_render_mermaid_does_not_emit_identity_or_command_line() -> None:
    rows = [LineageRow(10, 1, "node.exe", "2026-08-09T00:00:00+00:00", 0, "secret-value", False)]
    rendered = render_mermaid(rows, [10])
    assert "secret-value" not in rendered


def test_compare_lineage_reports_distinguishes_pid_reuse() -> None:
    previous = {"processes": [{"pid": 10, "created_at": "old"}, {"pid": 11, "created_at": "stable"}]}
    current = {"processes": [{"pid": 10, "created_at": "new"}, {"pid": 11, "created_at": "stable"}]}

    diff = compare_lineage_reports(current, previous)

    assert diff == {
        "baseline_available": True,
        "added_pids": [10],
        "removed_pids": [10],
        "stable_count": 1,
    }


def test_collect_lineage_counts_missing_required_attributes_as_inaccessible(
    monkeypatch,
) -> None:
    class FakeProcess:
        pid = 10
        info = {
            "pid": 10,
            "ppid": None,
            "name": "codex.exe",
            "cmdline": [],
            "create_time": 1.0,
        }

    monkeypatch.setattr(lineage.psutil, "process_iter", lambda attrs: [FakeProcess()])

    report = lineage.collect_lineage_snapshot("codex.exe")

    assert report["inaccessible_processes"] == 1
    assert report["process_count"] == 0
