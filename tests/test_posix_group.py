import os
import signal
from types import SimpleNamespace

import pytest

from runtime_process_guard.posix_group import PosixGroup

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='POSIX process groups')


@pytest.fixture
def owned(monkeypatch):
    import runtime_process_guard.posix_group as module

    calls = []
    process = SimpleNamespace(create_time=lambda: 100.0, uids=lambda: SimpleNamespace(real=os.getuid()))
    child = SimpleNamespace(pid=1234, returncode=None, poll=lambda: None)
    monkeypatch.setattr(module.psutil, 'Process', lambda pid: process)
    monkeypatch.setattr(module.os, 'getsid', lambda pid: 1234)
    monkeypatch.setattr(module.os, 'getpgid', lambda pid: 1234)
    monkeypatch.setattr(module.os, 'killpg', lambda group, sig: calls.append((group, sig)))
    return PosixGroup(child), child, process, calls


def test_only_owned_group_receives_term(owned):
    group, _, _, calls = owned
    assert group.terminate() is True
    assert calls == [(1234, 0), (1234, signal.SIGTERM)]


def test_replaced_generation_is_never_signalled(owned):
    group, _, process, calls = owned
    process.create_time = lambda: 101.0
    with pytest.raises(OSError, match='identity changed'):
        group.terminate()
    assert calls == [(1234, 0)]


def test_exited_leader_cannot_authorize_signal(owned):
    group, child, _, calls = owned
    child.returncode = 0
    with pytest.raises(OSError, match='leader ended'):
        group.terminate()
    assert calls == [(1234, 0)]


def test_disappeared_group_is_never_adopted_again(owned, monkeypatch):
    group, _, _, calls = owned

    def missing(*args):
        raise ProcessLookupError

    monkeypatch.setattr(os, 'killpg', missing)
    assert group.active_process_count() == 0
    monkeypatch.setattr(os, 'killpg', lambda *args: calls.append(args))
    group.terminate()
    assert calls == []


def test_observation_failure_never_authorizes_signal(owned, monkeypatch):
    group, _, _, calls = owned

    def denied(*args):
        raise PermissionError

    monkeypatch.setattr(os, 'killpg', denied)
    with pytest.raises(PermissionError):
        group.terminate()
    assert calls == []


def test_exiting_leader_observation_race_stays_protected_until_disappearance(owned, monkeypatch):
    import psutil
    from runtime_process_guard.guarded_stdio import _owned_job_alive

    group, child, _, calls = owned

    def missing(*args):
        raise psutil.NoSuchProcess(1234)

    monkeypatch.setattr(psutil, 'Process', missing)
    assert _owned_job_alive(group) is True
    with pytest.raises(OSError, match='unverified'):
        group.terminate()
    assert all(sig == 0 for _, sig in calls)
    child.returncode = 0

    def gone(*args):
        raise ProcessLookupError

    monkeypatch.setattr(os, 'killpg', gone)
    assert group.active_process_count() == 0
