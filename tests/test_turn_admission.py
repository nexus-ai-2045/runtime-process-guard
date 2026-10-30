import json
import multiprocessing
import os
from pathlib import Path
import time

import psutil
import pytest

from runtime_process_guard.turn_admission import Gate, event, key, main


def owner():
    return [os.getpid(), psutil.Process().create_time()]


def test_two_chats_fifo_and_late_completion(tmp_path):
    gate = Gate(tmp_path / "gate.json")
    a, b, c, d = (key(x) for x in "abcd")
    t = key("turn")
    assert gate.try_admit(a, t, owner(), owner())
    assert gate.try_admit(b, t, owner(), owner())
    assert not gate.try_admit(c, t, owner(), owner())
    assert not gate.try_admit(d, t, owner(), owner())
    gate.release(a, t)
    assert not gate.try_admit(d, t, owner(), owner())
    assert gate.try_admit(c, t, owner(), owner())
    assert gate.try_admit(c, key("next"), owner(), owner())
    gate.release(c, t)
    gate.release(c, t)  # old notify must not free the next turn
    assert not gate.try_admit(d, t, owner(), owner())


def test_same_chat_steering_does_not_consume_another_slot(tmp_path):
    gate = Gate(tmp_path / "gate.json")
    assert gate.try_admit(key("a"), key("one"), owner(), owner())
    assert gate.try_admit(key("b"), key("one"), owner(), owner())
    assert gate.try_admit(key("a"), key("two"), owner(), owner())
    assert not gate.try_admit(key("c"), key("one"), owner(), owner())


def wait_in_process(path, backend, done):
    admitted = Gate(Path(path)).acquire(key("c"), key("turn"), backend, wait_seconds=8)
    done.put(admitted)


def test_real_process_waits_then_auto_admits(tmp_path):
    path = tmp_path / "gate.json"
    gate = Gate(path)
    for session in ("a", "b"):
        assert gate.try_admit(key(session), key("turn"), owner(), owner())
    ctx = multiprocessing.get_context("spawn")
    done = ctx.Queue()
    process = ctx.Process(target=wait_in_process, args=(str(path), owner(), done))
    process.start()
    try:
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            if len(json.loads(path.read_text())["rows"]) == 3:
                break
            time.sleep(0.05)
        assert any(r["state"] == "waiting" for r in json.loads(path.read_text())["rows"])
        assert process.is_alive()
        gate.release(key("a"), key("turn"))
        assert done.get(timeout=6) is True
        process.join(timeout=2)
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.terminate()  # only this synthetic test-owned process
            process.join(timeout=2)


def test_timeout_removes_only_waiter_and_event_releases_exact_turn(tmp_path):
    gate = Gate(tmp_path / "gate.json")
    for session in ("a", "b"):
        assert gate.try_admit(key(session), key("turn"), owner(), owner())
    assert not gate.acquire(key("c"), key("turn"), owner(), wait_seconds=0.1)
    assert len(json.loads(gate.path.read_text())["rows"]) == 2
    event(gate, {"type": "agent-turn-complete", "thread-id": "a", "turn-id": "turn"})
    assert len(json.loads(gate.path.read_text())["rows"]) == 1


@pytest.mark.parametrize("raw", ["[]", "{bad", '{"schema":"wrong"}'])
def test_corrupt_state_and_bad_payload_fail_closed(tmp_path, capsys, raw):
    path = tmp_path / "gate.json"
    path.write_text(raw)
    payload = json.dumps({"type": "agent-turn-complete", "thread-id": "a", "turn-id": "t"})
    assert main(["--state", str(path), payload]) == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "block"
    assert path.read_text() == raw


def test_agent_flag_cannot_bypass_and_registered_child_reuses_parent(tmp_path):
    gate = Gate(tmp_path / "gate.json")
    assert gate.try_admit(key("a"), key("t"), owner(), owner())
    assert gate.try_admit(key("b"), key("t"), owner(), owner())
    assert not event(gate, {"hook_event_name": "UserPromptSubmit", "session_id": "c", "turn_id": "t", "agent_id": "c"})
    assert event(gate, {"hook_event_name": "SubagentStart", "session_id": "a", "agent_id": "child"})
    assert event(gate, {"hook_event_name": "UserPromptSubmit", "session_id": "child", "turn_id": "t", "agent_id": "child"})


def test_failed_release_is_replayed_before_next_admission(tmp_path, monkeypatch):
    gate = Gate(tmp_path / "gate.json")
    for session in ("a", "b"):
        gate.try_admit(key(session), key("t"), owner(), owner())
    original = gate._write
    def unavailable(_):
        raise OSError("busy")
    monkeypatch.setattr(gate, "_write", unavailable)
    with pytest.raises(RuntimeError, match="release-pending-replay"):
        gate.release(key("a"), key("t"))
    assert list(tmp_path.glob("*.releases/*.json"))
    monkeypatch.setattr(gate, "_write", original)
    assert gate.try_admit(key("c"), key("t"), owner(), owner())
    assert not list(tmp_path.glob("*.releases/*.json"))


@pytest.mark.parametrize("corruption", ["rewind", "duplicate"])
def test_ticket_corruption_rejected_without_repair(tmp_path, corruption):
    gate = Gate(tmp_path / "gate.json")
    for session in ("a", "b"):
        gate.try_admit(key(session), key("t"), owner(), owner())
    data = json.loads(gate.path.read_text())
    if corruption == "rewind":
        data["next_ticket"] = 0
    else:
        data["rows"][1]["ticket"] = data["rows"][0]["ticket"]
    gate.path.write_text(json.dumps(data))
    before = gate.path.read_bytes()
    with pytest.raises(ValueError, match="invalid-admission-row"):
        gate.try_admit(key("c"), key("t"), owner(), owner())
    assert gate.path.read_bytes() == before


def test_marker_cleanup_failure_does_not_block_or_reapply(tmp_path, monkeypatch):
    gate = Gate(tmp_path / "gate.json")
    for session in ("a", "b"):
        gate.try_admit(key(session), key("t"), owner(), owner())
    original = Path.unlink
    def fail_markers(path, *args, **kwargs):
        if path.parent.name.endswith(".releases"):
            raise PermissionError("busy")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", fail_markers)
    gate.release(key("a"), key("t"))
    assert gate.try_admit(key("a"), key("next"), owner(), owner())
    assert not gate.try_admit(key("c"), key("t"), owner(), owner())
    assert any(r["turn"] == key("next") and r["state"] == "active" for r in json.loads(gate.path.read_text())["rows"])


def test_pending_parent_release_denies_child(tmp_path, monkeypatch):
    gate = Gate(tmp_path / "gate.json")
    gate.try_admit(key("a"), key("t"), owner(), owner())
    gate.bind_child(key("a"), key("child"))
    original = gate._write
    monkeypatch.setattr(gate, "_write", lambda _: (_ for _ in ()).throw(OSError("busy")))
    with pytest.raises(RuntimeError):
        gate.release(key("a"), key("t"))
    monkeypatch.setattr(gate, "_write", original)
    assert not gate.child_admitted(key("child"), key("child"))


def test_marker_publication_has_finite_retry(tmp_path, monkeypatch):
    from runtime_process_guard import cli
    gate = Gate(tmp_path / "gate.json")
    gate.try_admit(key("a"), key("t"), owner(), owner())
    original, calls = cli._write_report, []
    def busy_once(path, payload):
        if path.parent.name.endswith(".releases"):
            calls.append(1)
            if len(calls) == 1:
                raise PermissionError("busy")
        return original(path, payload)
    monkeypatch.setattr(cli, "_write_report", busy_once)
    gate.release(key("a"), key("t"))
    assert len(calls) == 2
    assert not json.loads(gate.path.read_text())["rows"]


def test_cleanup_backlog_recovers_after_513_markers(tmp_path, monkeypatch):
    gate = Gate(tmp_path / "gate.json")
    gate.try_admit(key("a"), key("t"), owner(), owner())
    directory = gate.path.with_name(gate.path.name + ".releases")
    directory.mkdir()
    data = json.loads(gate.path.read_text())
    for i in range(512):
        identifier = f"{i:032x}"
        (directory / (identifier + ".json")).write_text(json.dumps({"id": identifier, "session": key("a"), "turn": key("old")}))
        data["applied_releases"].append(identifier)
    gate.path.write_text(json.dumps(data))
    identifier = f"{512:032x}"
    (directory / (identifier + ".json")).write_text(json.dumps({"id": identifier, "session": key("a"), "turn": key("old")}))
    ack = gate._ack
    monkeypatch.setattr(gate, "_ack", lambda _: None)
    with pytest.raises(ValueError, match="release-backlog-retry"):
        gate.try_admit(key("b"), key("t"), owner(), owner())
    monkeypatch.setattr(gate, "_ack", ack)
    with pytest.raises(ValueError, match="release-backlog-retry"):
        gate.try_admit(key("b"), key("t"), owner(), owner())
    assert gate.try_admit(key("b"), key("t"), owner(), owner())
    assert not list(directory.glob("*.json"))
