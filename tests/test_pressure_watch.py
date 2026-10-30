from types import SimpleNamespace

import pytest

from runtime_process_guard import pressure_watch as watch
from runtime_process_guard.lease_registry import LeaseRegistry, LeaseRegistryLockedError


@pytest.mark.parametrize("cpu,available,count,expected", [
    (20, 8192, 500, "continue"), (85, 8192, 500, "defer-new-work"),
    (20, 1024, 500, "defer-new-work"), (20, 8192, 1101, "defer-new-work"),
    (float("nan"), 8192, 500, "unknown"), (20, -1, 500, "unknown"),
    (20, 8192, True, "unknown"),
])
def test_pressure_decision(cpu, available, count, expected):
    assert watch.decision(cpu, available, count) == expected


def test_finite_watch_persists_no_raw_process_values(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(watch.time, "sleep", lambda _: None)
    monkeypatch.setattr(watch.psutil, "cpu_percent", lambda **_: 90)
    monkeypatch.setattr(watch.psutil, "pids", lambda: [1, 2])
    monkeypatch.setattr(watch.psutil, "virtual_memory", lambda: SimpleNamespace(available=8 * 1024**3))
    report = tmp_path / "report.json"
    assert watch.run(report, tmp_path / "stop", max_cycles=2) == 0
    value = json.loads(report.read_text())
    assert value["cycle"] == 2
    assert value["action"] == "defer-new-work"
    assert value["admission_enforced"] is False
    assert "pids" not in value["metrics"]


def test_duplicate_watch_and_failures_are_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(watch.time, "sleep", lambda _: None)
    monkeypatch.setattr(watch.psutil, "cpu_percent", lambda **_: 0)
    def unavailable():
        raise OSError("private detail")
    monkeypatch.setattr(watch.psutil, "virtual_memory", unavailable)
    report = tmp_path / "report.json"
    assert watch.run(report, tmp_path / "stop") == 40
    assert "private detail" not in report.read_text()
    with LeaseRegistry(report)._exclusive_lock():
        with pytest.raises(LeaseRegistryLockedError):
            watch.run(report, tmp_path / "stop", max_cycles=1)


def test_first_cpu_failure_is_anonymous(tmp_path, monkeypatch):
    monkeypatch.setattr(watch.time, "sleep", lambda _: None)
    def denied(**_):
        raise watch.psutil.AccessDenied(1, name="private detail")
    monkeypatch.setattr(watch.psutil, "cpu_percent", denied)
    report = tmp_path / "report.json"
    assert watch.main(["--report-path", str(report), "--stop-file", str(tmp_path / "stop")]) == 40
    assert "private detail" not in report.read_text()


def test_stop_during_wait_does_not_collect(tmp_path, monkeypatch):
    stop = tmp_path / "stop"
    monkeypatch.setattr(watch.time, "sleep", lambda _: stop.touch())
    monkeypatch.setattr(watch.psutil, "cpu_percent", lambda **_: pytest.fail("must not collect"))
    report = tmp_path / "report.json"
    assert watch.run(report, stop) == 0
    assert not report.exists()
