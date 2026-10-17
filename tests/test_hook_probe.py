import json
import os

import pytest

from runtime_process_guard.hook_probe import analyze_events, normalize_event, record_event


def event(phase='PreToolUse', call='call-1', **extra):
    return {'hook_event_name': phase, 'session_id': 'session-1', 'turn_id': 'turn-1',
            'tool_use_id': call, 'tool_name': 'mcp__codex_app__list_artifacts', **extra}


def test_only_selected_server_metadata_is_recorded():
    row = normalize_event(event(tool_input={'secret': 'private-value'}, tool_response={'secret': 'private-value'}), 'codex_app')
    text = json.dumps(row)
    assert 'private-value' not in text and 'session-1' not in text and 'call-1' not in text
    assert normalize_event(event(tool_name='mcp__other__read'), 'codex_app') is None


def test_complete_pair_never_proves_idle_or_ownership():
    rows = [normalize_event(event(p), 'codex_app') for p in ['PreToolUse', 'PostToolUse']]
    result = analyze_events(rows)
    assert result['call_state'] == 'no_in_flight_observed'
    assert result['binding_status'] == 'unverified_binding'
    assert result['stop_authorized'] is False and result['protection_required'] is True


def test_missing_end_remains_in_flight():
    assert analyze_events([normalize_event(event(), 'codex_app')])['call_state'] == 'in_flight_observed'


def test_missing_start_and_reverse_order_are_unknown():
    end = normalize_event(event('PostToolUse'), 'codex_app')
    start = normalize_event(event(), 'codex_app')
    assert analyze_events([end])['call_state'] == 'unknown'
    assert analyze_events([end, start])['call_state'] == 'unknown'


def test_duplicate_is_idempotent_and_shared_calls_stay_protected():
    start = normalize_event(event(), 'codex_app')
    end = normalize_event(event('PostToolUse'), 'codex_app')
    result = analyze_events([start, start, end, normalize_event(event(call='call-2'), 'codex_app')])
    assert result['call_count'] == 2 and result['call_state'] == 'in_flight_observed'
    assert result['stop_authorized'] is False


def test_pid_claim_is_not_an_authoritative_binding():
    row = normalize_event(event(owner_pid=123, connection_id='claimed'), 'codex_app')
    assert 'owner_pid' not in row and 'connection_id' not in row
    assert analyze_events([row])['binding_status'] == 'unverified_binding'


def test_missing_metadata_is_preserved_as_unknown():
    e = event()
    del e['tool_use_id']
    assert analyze_events([normalize_event(e, 'codex_app')])['call_state'] == 'unknown'
    assert analyze_events([])['call_state'] == 'unknown'


@pytest.mark.skipif(os.name != "posix", reason="POSIX private report files")
def test_recording_does_not_overwrite_and_uses_private_files(tmp_path):
    first = record_event(event(), 'codex_app', tmp_path)
    second = record_event(event('PostToolUse'), 'codex_app', tmp_path)
    assert first != second and len(list(tmp_path.glob('*.json'))) == 2
    assert all(p.stat().st_mode & 0o077 == 0 for p in tmp_path.glob('*.json'))


def test_delayed_exact_replay_does_not_reopen_completed_call():
    start = normalize_event(event(), 'codex_app')
    end = normalize_event(event('PostToolUse'), 'codex_app')
    result = analyze_events([start, end, start, end])
    assert result['call_state'] == 'no_in_flight_observed'
    assert result['duplicate_count'] == 2


def test_conflicting_tool_for_same_call_is_unknown():
    a = normalize_event(event(), 'codex_app')
    b = normalize_event(event('PostToolUse', tool_name='mcp__codex_app__list_projects'), 'codex_app')
    assert analyze_events([a, b])['call_state'] == 'unknown'


def test_same_call_id_across_sessions_is_not_merged():
    a = normalize_event(event(), 'codex_app')
    b = normalize_event(event(session_id='session-2'), 'codex_app')
    assert analyze_events([a, b])['call_count'] == 2


def test_malformed_event_phase_is_unknown():
    assert normalize_event(event(hook_event_name=[]), 'codex_app')['phase'] == 'unknown'


@pytest.mark.skipif(os.name != "posix", reason="POSIX private report files")
def test_capacity_exhaustion_never_overwrites(tmp_path, monkeypatch):
    import runtime_process_guard.hook_probe as module
    import pytest

    monkeypatch.setattr(module, 'LIMIT', 1)
    p = record_event(event(), 'codex_app', tmp_path)
    before = p.read_bytes()
    with pytest.raises(ValueError, match='capacity'):
        record_event(event('PostToolUse'), 'codex_app', tmp_path)
    assert p.read_bytes() == before


def test_diagnostic_self_report_is_metadata_only_and_never_grants_ownership():
    report = {'schema': 'runtime-process-guard/stdio-probe-v1',
              'observation_scope': 'self-process-metadata-only',
              'self': {'status': 'observed', 'pid': 123, 'created_at': '2026-10-16T00:00:00+00:00'},
              'secret': 'do-not-copy'}
    e = event('PostToolUse', tool_name='mcp__rpg_stdio_probe__stdio_owner_observation',
              tool_response={'content': [{'type': 'text', 'text': json.dumps(report)}]})
    row = normalize_event(e, 'rpg_stdio_probe')
    assert row['reported_process']['pid'] == 123
    assert 'do-not-copy' not in json.dumps(row)
    assert analyze_events([row])['stop_authorized'] is False


def test_hook_configuration_errors_cannot_deny_original_tool(tmp_path):
    import subprocess
    import sys
    import runtime_process_guard.hook_probe as module

    result = subprocess.run([sys.executable, module.__file__, '--server', 'codex_app'],
                            input=b'{}', capture_output=True, timeout=5)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {}


def test_missing_summary_directory_is_not_created(tmp_path):
    import subprocess
    import sys
    import runtime_process_guard.hook_probe as module

    target = tmp_path / 'absent'
    result = subprocess.run([sys.executable, module.__file__, '--server', 'codex_app',
                             '--report-dir', str(target), '--summarize'],
                            capture_output=True, timeout=5)
    assert not target.exists()
    assert json.loads(result.stdout)['stop_authorized'] is False
