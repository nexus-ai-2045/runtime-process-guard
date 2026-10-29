import json

import pytest

from runtime_process_guard.rpc_activity import RpcActivityTracker


def frame(**fields):
    return (json.dumps({'jsonrpc': '2.0', **fields}, ensure_ascii=False) + '\n').encode()


def test_bidirectional_typed_ids_and_chunked_utf8_require_all_replies():
    tracker = RpcActivityTracker()
    request = frame(id='1', method='仕事', params={'text': '日本語'})
    for byte in request:
        tracker.feed('client', bytes([byte]))
    tracker.feed('client', frame(id=1, method='integer'))
    tracker.feed('server', frame(id=1, method='sampling/createMessage'))
    assert tracker.pending_count == 3
    tracker.feed('server', frame(id='1', result={}))
    tracker.feed('server', frame(id=1, result={}))
    assert tracker.pending_count == 1
    tracker.feed('client', frame(id=1, result={}))
    assert not tracker.can_cleanup
    tracker.close_input()
    assert tracker.can_cleanup


def test_eof_does_not_cancel_pending_work_and_notifications_do_not_complete_it():
    tracker = RpcActivityTracker()
    tracker.feed('client', frame(id=7, method='long'))
    tracker.close_input()
    tracker.feed('server', frame(method='notifications/progress'))
    assert not tracker.can_cleanup
    tracker.feed('server', frame(id=7, result={}))
    assert tracker.can_cleanup


@pytest.mark.parametrize('data', [
    b'not-json\n', b'[]\n', b'{}\n', b'\xff\n',
    b'{"jsonrpc":"2.0","id":1,"id":2,"method":"x"}\n',
    b'{"jsonrpc":"2.0","method":"x","params":{"a":1,"a":2}}\n',
    frame(id=None, method='x'), frame(id=True, method='x'),
    frame(id=1.0, method='x'), frame(id=1, result={}),
    frame(id=1, method='x', result={}), frame(id=1, error={}),
    b'{"jsonrpc":"2.0","method":"x","params":{"a":NaN}}\n',
])
def test_invalid_frames_are_sticky_unknown(data):
    tracker = RpcActivityTracker()
    tracker.feed('client', data)
    tracker.close_input()
    tracker.feed('server', frame(method='notifications/ok'))
    assert tracker.unknown
    assert not tracker.can_cleanup


def test_duplicate_request_and_duplicate_response_fail_closed():
    for duplicate_response in (False, True):
        tracker = RpcActivityTracker()
        tracker.feed('client', frame(id=1, method='x'))
        if duplicate_response:
            tracker.feed('server', frame(id=1, result={}))
            tracker.feed('server', frame(id=1, result={}))
        else:
            tracker.feed('client', frame(id=1, method='y'))
        tracker.close_input()
        assert tracker.unknown
        assert not tracker.can_cleanup


def test_partial_both_directions_prevent_drain():
    for direction in ('client', 'server'):
        tracker = RpcActivityTracker()
        tracker.feed(direction, b'{"jsonrpc":')
        tracker.close_input()
        assert not tracker.can_cleanup


def test_pending_and_frame_bounds_fail_closed():
    tracker = RpcActivityTracker(max_pending=1)
    tracker.feed('client', frame(id=1, method='x') + frame(id=2, method='y'))
    assert tracker.unknown
    tracker = RpcActivityTracker(max_frame_bytes=8)
    tracker.feed('client', b'123456789')
    assert tracker.unknown


def test_output_request_after_input_eof_is_unknown_even_if_response_follows():
    tracker = RpcActivityTracker()
    tracker.close_input()
    assert tracker.begin_drain()
    tracker.feed('server', frame(id=2, method='sampling/createMessage'))
    tracker.feed('client', frame(id=2, result={}))
    assert tracker.unknown
    assert not tracker.can_cleanup


def test_no_raw_ids_or_completed_payload_are_retained():
    tracker = RpcActivityTracker()
    tracker.feed('client', frame(id='private-id', method='private-method', params={'key': 'secret'}))
    assert b'private' not in repr(tracker.__dict__).encode()
    assert b'secret' not in repr(tracker.__dict__).encode()


def test_output_completion_requires_successful_forwarding_and_no_inflight_bytes():
    tracker = RpcActivityTracker()
    tracker.feed('client', frame(id=1, method='x'))
    tracker.close_input()
    completion = tracker.begin_output(frame(id=1, result={}))
    assert tracker.pending_count == 1
    assert not tracker.can_cleanup
    tracker.finish_output(completion, success=True)
    assert tracker.can_cleanup
    completion = tracker.begin_output(frame(method='notification'))
    assert not tracker.can_cleanup
    tracker.finish_output(completion, success=False)
    assert tracker.unknown
    assert not tracker.can_cleanup


def test_output_reverse_request_admitted_before_blocking_forward():
    tracker = RpcActivityTracker()
    completion = tracker.begin_output(frame(id=1, method='reverse'))
    assert tracker.pending_count == 1
    tracker.feed('client', frame(id=1, result={}))
    tracker.close_input()
    assert not tracker.can_cleanup
    tracker.finish_output(completion, success=True)
    assert tracker.can_cleanup
