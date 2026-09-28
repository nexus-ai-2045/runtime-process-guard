import pytest

from runtime_process_guard import shadow
from runtime_process_guard.shadow import ProcessIdentityRow, group_generations


def test_groups_processes_started_within_window_into_generations() -> None:
    rows = [
        ProcessIdentityRow("a" * 64, 100.0),
        ProcessIdentityRow("b" * 64, 100.4),
        ProcessIdentityRow("a" * 64, 110.0),
        ProcessIdentityRow("b" * 64, 110.5),
    ]

    generations = group_generations(rows, window_seconds=2.0)

    assert len(generations) == 2
    assert generations[0].process_count == 2
    assert generations[0].unique_identity_count == 2
    assert generations[1].process_count == 2


def test_generation_output_contains_only_identity_prefixes() -> None:
    rows = [ProcessIdentityRow("a" * 64, 100.0)]
    generation = group_generations(rows)[0]
    assert generation.identity_prefixes == ("aaaaaaaaaaaa",)


class FakeProcess:
    info = {"name": "node.exe", "cmdline": ["node.exe"], "create_time": 100.0}

    def parent(self):
        return None


class NamedParent:
    def __init__(self, name: object, parent=None):
        self._name = name
        self._parent = parent

    def name(self):
        return self._name

    def parent(self):
        return self._parent


def test_missing_process_name_with_filter_is_incomplete(monkeypatch) -> None:
    missing = object()
    process = FakeProcess()
    process.info = {"name": missing, "cmdline": ["node.exe"], "create_time": 100.0}
    monkeypatch.setattr(shadow.psutil, "process_iter", lambda *args, **kwargs: [process])

    report = shadow.collect_shadow_snapshot("codex.exe", process_name="node.exe")

    assert report["inaccessible_processes"] == 1
    assert report["observation_complete"] is False


def test_scan_exception_is_anonymous_incomplete(monkeypatch) -> None:
    def explode(*args, **kwargs):
        raise RuntimeError("secret command line must not escape")

    monkeypatch.setattr(shadow.psutil, "process_iter", explode)

    report = shadow.collect_shadow_snapshot("codex.exe")

    assert report["inaccessible_processes"] == 1
    assert report["observation_complete"] is False
    assert "secret command" not in str(report)


def test_owned_process_without_cmdline_or_creation_time_is_incomplete(monkeypatch) -> None:
    process = FakeProcess()
    process.info = {"name": "node.exe", "cmdline": shadow._MISSING, "create_time": 100.0}
    process.parent = lambda: NamedParent("codex.exe")
    monkeypatch.setattr(shadow.psutil, "process_iter", lambda *args, **kwargs: [process])

    report = shadow.collect_shadow_snapshot("codex.exe")

    assert report["inaccessible_processes"] == 1
    assert report["observation_complete"] is False


def test_owner_chain_depth_limit_is_incomplete(monkeypatch) -> None:
    chain = None
    for _ in range(13):
        chain = NamedParent("launcher.exe", chain)
    process = FakeProcess()
    process.parent = lambda: chain
    monkeypatch.setattr(shadow.psutil, "process_iter", lambda *args, **kwargs: [process])

    report = shadow.collect_shadow_snapshot("codex.exe")

    assert report["inaccessible_processes"] == 1
    assert report["observation_complete"] is False


@pytest.mark.parametrize("created_at", [float("nan"), float("inf"), 1e300])
def test_owned_process_with_nonfinite_creation_time_is_incomplete(monkeypatch, created_at) -> None:
    process = FakeProcess()
    process.info = {"name": "node.exe", "cmdline": ["node.exe"], "create_time": created_at}
    process.parent = lambda: NamedParent("codex.exe")
    monkeypatch.setattr(shadow.psutil, "process_iter", lambda *args, **kwargs: [process])

    report = shadow.collect_shadow_snapshot("codex.exe")

    assert report["inaccessible_processes"] == 1
    assert report["observation_complete"] is False


def test_owned_process_with_non_string_cmdline_is_incomplete(monkeypatch) -> None:
    process = FakeProcess()
    process.info = {"name": "node.exe", "cmdline": ["node.exe", 123], "create_time": 100.0}
    process.parent = lambda: NamedParent("codex.exe")
    monkeypatch.setattr(shadow.psutil, "process_iter", lambda *args, **kwargs: [process])

    report = shadow.collect_shadow_snapshot("codex.exe")

    assert report["inaccessible_processes"] == 1
    assert report["observation_complete"] is False


def test_unrelated_process_missing_attributes_does_not_invalidate_snapshot(monkeypatch) -> None:
    process = FakeProcess()
    process.info = {"name": "node.exe", "cmdline": None, "create_time": None}
    monkeypatch.setattr(shadow.psutil, "process_iter", lambda *args, **kwargs: [process])

    report = shadow.collect_shadow_snapshot("codex.exe", process_name="node.exe")

    assert report["inaccessible_processes"] == 0
    assert report["process_count"] == 0
    assert report["observation_complete"] is True
