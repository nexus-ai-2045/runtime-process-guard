import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != 'darwin', reason='macOS stdio integration')


def launch(tmp_path, command, *extra, bootstrap=None):
    env = os.environ.copy()
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1] / 'src')
    prefix = [sys.executable, '-m', 'runtime_process_guard.cli'] if bootstrap is None else [sys.executable, '-c', bootstrap]
    return subprocess.Popen([
        *prefix, 'guarded-stdio',
        '--lease-state', str(tmp_path / 'leases.json'), '--grace-seconds', '0.5',
        '--min-available-memory-mb', '0', '--max-cpu-percent', '1000',
        '--max-server-instances', '1', '--max-total-instances', '1',
        *extra, '--', *command,
    ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)


def read_line(stream):
    result = queue.Queue()
    threading.Thread(target=lambda: result.put(stream.readline()), daemon=True).start()
    return result.get(timeout=6)


def test_connected_request_survives_idle_and_eof_releases_lease(tmp_path):
    child = launch(tmp_path, [sys.executable, '-u', '-c',
        'import sys,time; line=sys.stdin.buffer.readline(); time.sleep(0.3); '
        'sys.stdout.buffer.write(line); sys.stdout.buffer.flush(); sys.stdin.buffer.read()'],
        '--idle-seconds', '0.05')
    try:
        child.stdin.write(b'{"jsonrpc":"2.0","id":1,"method":"slow"}\n')
        child.stdin.flush()
        assert b'"id":1' in read_line(child.stdout)
        assert child.poll() is None
        child.stdin.close()
        code = child.wait(timeout=8)
        assert code == 0, child.stderr.read().decode()
        assert json.loads((tmp_path / 'leases.json').read_text())['leases'] == []
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_capacity_refuses_new_connection_without_stopping_active_one(tmp_path):
    command = [sys.executable, '-u', '-c',
        'import sys; print("ready",flush=True); sys.stdin.buffer.read()']
    first = launch(tmp_path, command)
    try:
        assert read_line(first.stdout) == b'ready\n'
        second = launch(tmp_path, command)
        out, err = second.communicate(timeout=8)
        assert second.returncode == 20
        assert out == b''
        assert b'lease-capacity' in err
        assert first.poll() is None
        first.stdin.close()
        assert first.wait(timeout=8) == 0
    finally:
        if first.poll() is None:
            first.kill()
            first.wait()


def test_stubborn_server_receives_term_only_after_client_eof(tmp_path):
    child = launch(tmp_path, [sys.executable, '-u', '-c',
        'import sys,time; print("ready",flush=True); sys.stdin.buffer.read(); time.sleep(60)'])
    try:
        assert read_line(child.stdout) == b'ready\n'
        time.sleep(0.15)
        assert child.poll() is None
        child.stdin.close()
        assert child.wait(timeout=8) in (0, -15)
        assert json.loads((tmp_path / 'leases.json').read_text())['leases'] == []
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_dead_guard_reservation_is_not_reused_with_surviving_child(tmp_path):
    import psutil

    first = launch(tmp_path, [sys.executable, '-u', '-c',
        'import os,sys,time; print(os.getpid(),flush=True); sys.stdin.buffer.read(); time.sleep(60)'])
    owned = None
    try:
        owned = psutil.Process(int(read_line(first.stdout)))
        first.kill()
        first.wait(timeout=5)
        assert owned.is_running()
        second = launch(tmp_path, [sys.executable, '-c', 'pass'])
        out, err = second.communicate(timeout=8)
        assert second.returncode == 20
        assert b'lease-capacity' in err
        assert json.loads((tmp_path / 'leases.json').read_text())['leases']
    finally:
        if first.poll() is None:
            first.kill()
            first.wait()
        if owned is not None and owned.is_running():
            owned.terminate()


def test_stdout_eof_alone_does_not_stop_connected_server(tmp_path):
    child = launch(tmp_path, [sys.executable, '-u', '-c',
        'import os,sys; print("ready",flush=True); os.close(1); sys.stdin.buffer.read()'])
    try:
        assert read_line(child.stdout) == b'ready\n'
        time.sleep(.2)
        assert child.poll() is None
        child.stdin.close()
        assert child.wait(timeout=8) == 0
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_retained_registry_cannot_be_downgraded_by_legacy_reader(tmp_path):
    from runtime_process_guard.lease_registry import LeaseRegistry, LeaseRegistryMalformedError

    child = launch(tmp_path, [sys.executable, '-u', '-c',
        'import sys; print("ready",flush=True); sys.stdin.buffer.read()'])
    try:
        assert read_line(child.stdout) == b'ready\n'
        with pytest.raises(LeaseRegistryMalformedError):
            LeaseRegistry(tmp_path / 'leases.json').read()
        child.stdin.close()
        assert child.wait(timeout=8) == 0
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_term_resistant_child_is_reported_and_reservation_retained(tmp_path):
    import psutil

    child = launch(tmp_path, [sys.executable, '-u', '-c',
        'import os,sys,time,signal; signal.signal(signal.SIGTERM,signal.SIG_IGN); '
        'print(os.getpid(),flush=True); sys.stdin.buffer.read(); time.sleep(60)'])
    owned = None
    try:
        owned = psutil.Process(int(read_line(child.stdout)))
        child.stdin.close()
        assert child.wait(timeout=8) == 40
        assert owned.is_running()
        assert json.loads((tmp_path / 'leases.json').read_text())['leases']
        assert b'Traceback' not in child.stderr.read()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        if owned is not None and owned.is_running():
            owned.kill()


def test_heartbeat_failure_protects_connected_request_and_retains_reservation(tmp_path):
    bootstrap = (
        'import runtime_process_guard.guarded_stdio as g; '
        'g._heartbeat_with_retry=lambda *a:(_ for _ in ()).throw(g.LeaseRegistryError("failure")); '
        'from runtime_process_guard.cli import main; raise SystemExit(main())'
    )
    child = launch(tmp_path, [sys.executable, '-u', '-c',
        'import sys; print("ready",flush=True); sys.stdin.buffer.read()'],
        '--lease-ttl-seconds', '0.3', bootstrap=bootstrap)
    try:
        assert read_line(child.stdout) == b'ready\n'
        time.sleep(.5)
        assert child.poll() is None
        child.stdin.close()
        assert child.wait(timeout=8) == 40
        assert json.loads((tmp_path / 'leases.json').read_text())['leases']
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


@pytest.mark.parametrize('leader_ended', [False, True])
def test_ownership_setup_failure_never_kills_or_releases_unverified_group(tmp_path, monkeypatch, leader_ended):
    import runtime_process_guard.guarded_stdio as module
    from runtime_process_guard.admission import Observation

    class Child:
        pid = 12345678
        killed = False
        returncode = 0 if leader_ended else None

        def poll(self):
            return self.returncode

        def terminate(self):
            pass

        def kill(self):
            self.killed = True
            self.returncode = -9

        def wait(self, timeout):
            if self.returncode is None:
                raise subprocess.TimeoutExpired('private-command', timeout)
            return self.returncode

    child = Child()
    monkeypatch.setattr(module, 'observe', lambda *a, **k: Observation(
        identity='a' * 64, executable='python', duplicate_pids=(),
        available_memory_mb=8192, cpu_percent=0, reuse_policy='dedicated-stdio'))
    monkeypatch.setattr(module.subprocess, 'Popen', lambda *a, **k: child)

    def unavailable(*args):
        raise OSError('unavailable')

    monkeypatch.setattr(module, 'PosixGroup', unavailable)
    options = module.validate_guard_options(mode=module.GuardMode.SHADOW,
        idle_seconds=1, grace_seconds=.1, command=['private-command'],
        lease_state=tmp_path / 'leases.json')
    with pytest.raises(OSError):
        module.run_guarded_stdio(options)
    assert child.killed is False
    assert json.loads((tmp_path / 'leases.json').read_text())['leases']


def test_downstream_stdin_failure_keeps_observing_client_eof(tmp_path):
    import psutil

    owned = None
    child = launch(tmp_path, [sys.executable, '-u', '-c',
        'import os,time; os.close(0); print("ready",flush=True); time.sleep(60)'])
    try:
        assert read_line(child.stdout) == b'ready\n'
        owned = psutil.Process(child.pid).children()[0]
        child.stdin.write(b'request\n')
        child.stdin.flush()
        time.sleep(.2)
        assert child.poll() is None
        child.stdin.close()
        assert child.wait(timeout=8) == 0
        assert json.loads((tmp_path / 'leases.json').read_text())['leases'] == []
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=8)

        if owned is not None and owned.is_running():
            owned.terminate()


def test_server_self_term_during_grace_is_not_reported_as_success(tmp_path):
    gate = tmp_path / 'release-server'
    child = launch(tmp_path, [sys.executable, '-u', '-c',
        'import os,sys,signal,time; from pathlib import Path; '
        'print("ready",flush=True); sys.stdin.buffer.read(); '
        'exec("while not Path(sys.argv[1]).exists(): time.sleep(.01)"); '
        'os.kill(os.getpid(),signal.SIGTERM)', str(gate)], '--grace-seconds', '3')
    try:
        assert read_line(child.stdout) == b'ready\n'
        child.stdin.close()
        while json.loads(read_line(child.stderr)).get('event') != 'graceful-shutdown-requested':
            pass
        gate.touch()
        assert child.wait(timeout=8) == 241
        assert json.loads((tmp_path / 'leases.json').read_text())['leases'] == []
    finally:
        gate.touch()
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=8)
