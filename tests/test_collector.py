from __future__ import annotations

import os
from types import SimpleNamespace

import psutil

from runtime_process_guard.admission import Budget, evaluate
from runtime_process_guard.collector import observe
from runtime_process_guard.privacy import command_identity


COMMAND = ("python", "worker.py")


def _process_iter(monkeypatch, processes: list[object]) -> None:
    monkeypatch.setattr(
        psutil,
        "process_iter",
        lambda attrs: iter(processes),
    )
    monkeypatch.setattr(
        psutil,
        "virtual_memory",
        lambda: SimpleNamespace(available=8 * 1024 * 1024 * 1024),
    )
    monkeypatch.setattr(psutil, "cpu_percent", lambda interval: 10.0)


def _process(pid: int, cmdline: object) -> SimpleNamespace:
    return SimpleNamespace(pid=pid, info={"pid": pid, "cmdline": cmdline})


def test_missing_cmdline_is_inaccessible_and_singleton_is_unknown(monkeypatch) -> None:
    _process_iter(monkeypatch, [_process(101, None)])

    observation = observe(COMMAND)
    result = evaluate(observation, Budget())

    assert observation.inaccessible_processes == 1
    assert result.decision == "unknown"


def test_missing_cmdline_key_is_inaccessible(monkeypatch) -> None:
    process = SimpleNamespace(pid=101, info={"pid": 101})
    _process_iter(monkeypatch, [process])

    observation = observe(COMMAND)

    assert observation.inaccessible_processes == 1


def test_dedicated_stdio_allows_missing_cmdline_when_resources_are_healthy(monkeypatch) -> None:
    _process_iter(monkeypatch, [_process(101, None)])

    result = evaluate(observe(COMMAND, reuse_policy="dedicated-stdio"), Budget())

    assert result.decision == "allow"
    assert result.inaccessible_processes == 1


def test_pressure_defers_even_for_dedicated_stdio_with_inaccessible_process(monkeypatch) -> None:
    _process_iter(monkeypatch, [_process(101, None)])
    monkeypatch.setattr(
        psutil,
        "virtual_memory",
        lambda: SimpleNamespace(available=500 * 1024 * 1024),
    )

    result = evaluate(
        observe(COMMAND, reuse_policy="dedicated-stdio"),
        Budget(min_available_memory_mb=2048),
    )

    assert result.decision == "defer"


def test_empty_cmdline_is_accessible_and_does_not_block_admission(monkeypatch) -> None:
    _process_iter(monkeypatch, [_process(101, [])])

    observation = observe(COMMAND)

    assert observation.inaccessible_processes == 0
    assert evaluate(observation, Budget()).decision == "allow"


def test_matching_processes_are_all_counted_and_self_is_excluded(monkeypatch) -> None:
    identity = command_identity(COMMAND)
    _process_iter(
        monkeypatch,
        [
            _process(os.getpid(), list(COMMAND)),
            _process(101, list(COMMAND)),
            _process(102, list(COMMAND)),
        ],
    )

    observation = observe(COMMAND)

    assert observation.identity == identity
    assert observation.duplicate_pids == (101, 102)


def test_malformed_cmdline_is_inaccessible_without_exposing_exception(monkeypatch) -> None:
    _process_iter(monkeypatch, [_process(101, "python worker.py")])

    observation = observe(COMMAND)

    assert observation.inaccessible_processes == 1
    assert observation.collection_errors == ()
