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


def _stop_owned_fixture(guard, owned):
    # These are only PIDs created by this test, never existing runtime processes.
    if owned is not None:
        try:
            owned.kill()
            owned.wait(timeout=3)
        except (ProcessLookupError, __import__('psutil').NoSuchProcess):
            pass
    if guard.poll() is None:
        guard.kill()
        guard.wait(timeout=3)


def test_enforce_holds_pending_after_eof_then_reclaims_after_response(tmp_path):
    import psutil

    gate = tmp_path / 'allow-response'
    command = [sys.executable, '-u', '-c',
        'import sys,time,json,os; from pathlib import Path; '
        'print(json.dumps({"jsonrpc":"2.0","method":"ready","params":{"pid":os.getpid()}}),flush=True); '
        'req=json.loads(sys.stdin.readline()); '
        'exec("while not Path(sys.argv[1]).exists(): time.sleep(.01)"); '
        'print(json.dumps({"jsonrpc":"2.0","id":req["id"],"result":{}}),flush=True); '
        'sys.stdin.read(); time.sleep(60)', str(gate)]
    guard = launch(tmp_path, command, '--mode', 'enforce', '--track-requests', '--idle-seconds', '.05')
    owned = None
    try:
        owned = psutil.Process(json.loads(read_line(guard.stdout))['params']['pid'])
        guard.stdin.write(b'{"jsonrpc":"2.0","id":7,"method":"slow"}\n')
        guard.stdin.flush()
        guard.stdin.close()
        time.sleep(.8)
        assert guard.poll() is None
        assert owned.is_running()
        gate.touch()
        assert json.loads(read_line(guard.stdout))['id'] == 7
        assert guard.wait(timeout=5) == 0
        assert json.loads((tmp_path / 'leases.json').read_text())['leases'] == []
        assert b'"reason": "requests-drained"' in guard.stderr.read()
    finally:
        gate.touch()
        _stop_owned_fixture(guard, owned)


@pytest.mark.parametrize('data,protected', [
    (b'not-json\n', None),
    (b'{"jsonrpc":', None),
    (b'{"jsonrpc":"2.0","id":7,"method":"pending"}\n', None),
    (b'', 'discord'), (b'', 'orca'),
])
def test_enforce_retains_unknown_partial_pending_and_protected(tmp_path, data, protected):
    import psutil

    extra = ['--mode', 'enforce', '--track-requests']
    if protected:
        extra += ['--protected-server', protected]
    guard = launch(tmp_path, [sys.executable, '-u', '-c',
        'import sys,time,json; print(json.dumps({"jsonrpc":"2.0","method":"ready"}),flush=True); '
        'sys.stdin.read(); time.sleep(60)'], *extra)
    owned = None
    try:
        assert json.loads(read_line(guard.stdout))['method'] == 'ready'
        owned = psutil.Process(guard.pid).children()[0]
        guard.stdin.write(data)
        guard.stdin.close()
        time.sleep(.85)
        assert guard.poll() is None
        assert owned.is_running()
        assert json.loads((tmp_path / 'leases.json').read_text())['leases']
    finally:
        _stop_owned_fixture(guard, owned)


def test_enforce_connected_idle_is_never_reclaimed(tmp_path):
    import psutil

    guard = launch(tmp_path, [sys.executable, '-u', '-c',
        'import sys,time,json; print(json.dumps({"jsonrpc":"2.0","method":"ready"}),flush=True); '
        'sys.stdin.read(); time.sleep(60)'], '--mode', 'enforce', '--track-requests', '--idle-seconds', '.01')
    owned = None
    try:
        assert json.loads(read_line(guard.stdout))['method'] == 'ready'
        owned = psutil.Process(guard.pid).children()[0]
        time.sleep(.85)
        assert guard.poll() is None
        assert owned.is_running()
        guard.stdin.close()
        assert guard.wait(timeout=5) == 0
    finally:
        _stop_owned_fixture(guard, owned)


def test_large_bidirectional_frames_do_not_lock_out_input_during_blocked_output(tmp_path):
    import psutil

    server = '''import sys,json,time,os,threading
print(json.dumps({'jsonrpc':'2.0','method':'ready','params':{'pid':os.getpid()}}),flush=True)
json.loads(sys.stdin.readline())
def send_reverse():
    print(json.dumps({'jsonrpc':'2.0','id':'reverse','method':'sampling','params':{'text':'x'*400000}}),flush=True)
writer=threading.Thread(target=send_reverse)
writer.start()
request=json.loads(sys.stdin.readline())
writer.join()
print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':{}}),flush=True)
sys.stdin.read()
time.sleep(60)
'''
    guard = launch(tmp_path, [sys.executable, '-u', '-c', server], '--mode', 'enforce', '--track-requests')
    owned = None
    write_done = threading.Event()
    try:
        owned = psutil.Process(json.loads(read_line(guard.stdout))['params']['pid'])
        guard.stdin.write(b'{"jsonrpc":"2.0","method":"start"}\n')
        guard.stdin.flush()
        # Let stdout fill, then make the client fill stdin without reading stdout.
        time.sleep(.15)
        payload = (json.dumps({'jsonrpc':'2.0','id':'large','method':'large',
                              'params':{'text':'y'*400000}}) + '\n').encode()
        def send_large():
            try:
                guard.stdin.write(payload)
                guard.stdin.flush()
                write_done.set()
            except (BrokenPipeError, ValueError):
                pass
        threading.Thread(target=send_large, daemon=True).start()
        assert write_done.wait(4), 'input must progress while output is blocked'
        assert json.loads(read_line(guard.stdout))['id'] == 'reverse'
        assert json.loads(read_line(guard.stdout))['id'] == 'large'
        guard.stdin.write(b'{"jsonrpc":"2.0","id":"reverse","result":{}}\n')
        guard.stdin.close()
        assert guard.wait(timeout=5) == 0
    finally:
        _stop_owned_fixture(guard, owned)


@pytest.mark.parametrize('tail', [
    b'{"jsonrpc":',
    b'{"jsonrpc":"2.0","id":9,"method":"reverse"}\n',
])
def test_enforce_revokes_drain_for_output_partial_or_reverse_request(tmp_path, tail):
    import psutil

    server = ('import sys,time,os,json; '
        'print(json.dumps({"jsonrpc":"2.0","method":"ready","params":{"pid":os.getpid()}}),flush=True); '
        'sys.stdin.read(); time.sleep(.15); '
        f'sys.stdout.buffer.write({tail!r}); sys.stdout.buffer.flush(); time.sleep(60)')
    guard = launch(tmp_path, [sys.executable, '-u', '-c', server], '--mode', 'enforce', '--track-requests')
    owned = None
    try:
        owned = psutil.Process(json.loads(read_line(guard.stdout))['params']['pid'])
        guard.stdin.close()
        time.sleep(.9)
        assert guard.poll() is None
        assert owned.is_running()
        assert json.loads((tmp_path / 'leases.json').read_text())['leases']
    finally:
        _stop_owned_fixture(guard, owned)


def test_enforce_owner_death_does_not_close_live_admission_or_authorize_cleanup(tmp_path):
    import psutil
    from datetime import datetime, timezone

    owner = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    owner_created = datetime.fromtimestamp(psutil.Process(owner.pid).create_time(), timezone.utc).isoformat()
    server = ('import sys,time,os,json; '
        'print(json.dumps({"jsonrpc":"2.0","method":"ready","params":{"pid":os.getpid()}}),flush=True); '
        'sys.stdin.read(); time.sleep(60)')
    guard = launch(tmp_path, [sys.executable, '-u', '-c', server],
        '--mode', 'enforce', '--track-requests', '--client-owner-pid', str(owner.pid),
        '--client-owner-created-at', owner_created)
    owned = None
    try:
        owned = psutil.Process(json.loads(read_line(guard.stdout))['params']['pid'])
        owner.terminate()
        owner.wait(timeout=3)
        time.sleep(.7)
        assert guard.poll() is None
        assert owned.is_running()
        guard.stdin.close()
        time.sleep(.7)
        assert guard.poll() is None
        assert owned.is_running()
    finally:
        _stop_owned_fixture(guard, owned)
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=3)
