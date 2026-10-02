import io
import json
import os

import pytest

from runtime_process_guard import stdio_probe as probe


def identity(pid):
    return {"pid": pid, "status": "observed", "created_at": "2026-10-02T00:00:00+00:00"}


def observation(peer=123):
    return {
        "status": "observed",
        "file_type": 3,
        "pipe_info": {"success": True, "error": 0},
        "server": {"success": True, "error": 0, "pid": peer},
        "client": {"success": True, "error": 0, "pid": os.getpid()},
    }


def request(method, params=None, request_id=1):
    result = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        result["params"] = params
    return result


def serve(frames, collector=lambda: {"ownership_status": "unknown"}):
    incoming = io.BytesIO(
        b"".join(json.dumps(frame).encode() + b"\n" for frame in frames)
    )
    outgoing, diagnostics = io.StringIO(), io.StringIO()
    code = probe.run_probe_mcp(
        stdin=incoming, stdout=outgoing, stderr=diagnostics, collector=collector
    )
    return (
        code,
        [json.loads(line) for line in outgoing.getvalue().splitlines()],
        diagnostics.getvalue(),
    )


def init():
    return request("initialize", {"protocolVersion": "2025-06-18"})


def test_valid_notifications_receive_no_reply_and_keep_connection_alive():
    code, responses, diagnostics = serve(
        [
            init(),
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {
                "jsonrpc": "2.0",
                "method": "notifications/cancelled",
                "params": {"requestId": 88},
            },
            {"jsonrpc": "2.0", "method": "notifications/unknown"},
            request("tools/list", request_id=2),
        ]
    )
    assert code == 0
    assert [response["id"] for response in responses] == [1, 2]
    assert responses[1]["result"]["tools"][0]["name"] == "stdio_owner_observation"
    assert json.loads(diagnostics)["ownership_status"] == "unknown"


def test_matching_peers_are_candidates_never_owners():
    report = probe.collect_stdio_probe(
        platform="win32", query=lambda _: observation(), identity=identity
    )
    assert report["candidate_status"] == "consistent"
    assert report["ownership_status"] == "unknown"
    assert report["authority_granted"] is False
    assert [item["pid"] for item in report["peer_candidates"]] == [123]


def test_failed_api_does_not_prove_peer_consistency():
    def query(stream):
        result = observation()
        if stream == "stdout":
            result["client"] = {"success": False, "error": 5}
        return result

    report = probe.collect_stdio_probe(platform="win32", query=query, identity=identity)
    assert report["candidate_status"] == "unknown"
    assert report["streams"]["stdout"]["client"]["error"] == 5


def test_pipe_info_failure_never_labels_pipe_named_or_anonymous():
    report = probe.collect_stdio_probe(
        platform="win32",
        query=lambda _: {
            "status": "observed",
            "file_type": 3,
            "pipe_info": {"success": False, "error": 1},
        },
        identity=identity,
    )
    assert report["candidate_status"] == "unknown"
    assert "named" not in json.dumps(report)
    assert "anonymous" not in json.dumps(report)


def test_peer_mismatch_is_unknown():
    report = probe.collect_stdio_probe(
        platform="win32",
        query=lambda stream: observation(123 if stream == "stdin" else 456),
        identity=identity,
    )
    assert report["candidate_status"] == "unknown"


def test_self_only_is_not_candidate():
    report = probe.collect_stdio_probe(
        platform="win32", query=lambda _: observation(os.getpid()), identity=identity
    )
    assert report["peer_candidates"] == []
    assert report["candidate_status"] == "unknown"


def test_nonwindows_does_not_construct_adapter(monkeypatch):
    monkeypatch.setattr(
        probe, "WindowsStdioQueries", lambda: pytest.fail("adapter was constructed")
    )
    report = probe.collect_stdio_probe(platform="linux")
    assert report["probe_status"] == "unsupported"
    assert report["ownership_status"] == "unknown"


def test_windows_adapter_is_injectable_across_platforms(monkeypatch):
    monkeypatch.setattr(probe, "WindowsStdioQueries", lambda: lambda _: observation())
    assert (
        probe.collect_stdio_probe(platform="win32", identity=identity)["probe_status"]
        == "collected"
    )


def test_adapter_failure_is_sanitized():
    def query(_):
        raise OSError("secret private path")

    report = probe.collect_stdio_probe(platform="win32", query=query)
    assert report["probe_status"] == "unknown"
    assert "secret" not in json.dumps(report)


def test_observation_only_exports_allowed_metadata():
    raw = observation()
    raw["argv"] = "secret"
    raw["server"]["bytes"] = "secret"
    report = probe.build_stdio_report(
        {"stdin": raw, "stdout": raw, "env": "secret"},
        self_pid=os.getpid(),
        identity=identity,
    )
    assert "secret" not in json.dumps(report)


def test_windows_queries_capture_api_failures_without_handle_output(monkeypatch):
    import ctypes

    errors = {"last": 0}
    monkeypatch.setattr(
        ctypes, "set_last_error", lambda error: errors.update(last=error), raising=False
    )
    monkeypatch.setattr(ctypes, "get_last_error", lambda: errors["last"], raising=False)

    class Kernel:
        def GetStdHandle(self, _):
            return 987654

        def GetFileType(self, _):
            return 3

        def GetNamedPipeInfo(self, *args):
            errors["last"] = 1
            return 0

        def GetNamedPipeServerProcessId(self, handle, output):
            output._obj.value = 123
            return 1

        def GetNamedPipeClientProcessId(self, handle, output):
            errors["last"] = 5
            return 0

    adapter = probe.WindowsStdioQueries.__new__(probe.WindowsStdioQueries)
    adapter.kernel, adapter.dword = Kernel(), ctypes.c_uint32
    result = adapter("stdin")
    assert result["pipe_info"] == {"success": False, "error": 1}
    assert result["server"] == {"success": True, "error": 0, "pid": 123}
    assert result["client"] == {"success": False, "error": 5}
    assert "987654" not in json.dumps(result)


def test_initialize_list_call_and_eof_receipt():
    code, responses, diagnostics = serve(
        [
            init(),
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            request("tools/list", request_id="日本語"),
            request(
                "tools/call", {"name": "stdio_owner_observation", "arguments": {}}, -2
            ),
        ]
    )
    assert code == 0
    assert len(responses) == 3
    assert responses[0]["result"]["protocolVersion"] == "2025-06-18"
    assert responses[1]["id"] == "日本語"
    assert responses[1]["result"]["tools"][0]["annotations"]["readOnlyHint"] is True
    assert (
        json.loads(responses[2]["result"]["content"][0]["text"])["ownership_status"]
        == "unknown"
    )
    assert json.loads(diagnostics)["event"] == "stdio_probe_eof"


@pytest.mark.parametrize(
    "frame",
    [
        [],
        request("tools/list", request_id=True),
        request("tools/list", request_id=None),
        request("tools/list", request_id="a" * 257),
        request("tools/list", []),
    ],
)
def test_invalid_frames_fail_closed(frame):
    code, responses, diagnostics = serve([frame])
    assert code == 40
    assert responses[0]["error"]["code"] == -32600
    assert not diagnostics


def test_oversized_frame_is_bounded_and_no_echo():
    outgoing = io.StringIO()
    code = probe.run_probe_mcp(
        stdin=io.BytesIO(b"secret" * 20000 + b"\n"),
        stdout=outgoing,
        stderr=io.StringIO(),
    )
    assert code == 40
    assert "secret" not in outgoing.getvalue()


def test_invalid_utf8_is_not_echoed():
    outgoing = io.StringIO()
    assert (
        probe.run_probe_mcp(
            stdin=io.BytesIO(b"\xff\n"), stdout=outgoing, stderr=io.StringIO()
        )
        == 40
    )
    assert json.loads(outgoing.getvalue())["error"]["code"] == -32600


def test_unsupported_version_negotiates_known_version():
    code, responses, _ = serve(
        [request("initialize", {"protocolVersion": "future"}), request("tools/list")]
    )
    assert code == 0
    assert responses[0]["result"]["protocolVersion"] == "2025-06-18"
    assert "tools" in responses[1]["result"]


@pytest.mark.parametrize("version", [None, True, 123, "", []])
def test_invalid_version_does_not_initialize(version):
    _, responses, _ = serve(
        [request("initialize", {"protocolVersion": version}), request("tools/list")]
    )
    assert [item["error"]["code"] for item in responses] == [-32602, -32600]


def test_unknown_tool_does_not_collect():
    code, responses, _ = serve(
        [init(), request("tools/call", {"name": "kill"})],
        collector=lambda: pytest.fail("collector called"),
    )
    assert code == 0
    assert responses[1]["error"]["code"] == -32602


def test_tool_rejects_arguments():
    _, responses, _ = serve(
        [
            init(),
            request(
                "tools/call",
                {"name": "stdio_owner_observation", "arguments": {"path": "secret"}},
            ),
        ]
    )
    assert responses[-1]["error"]["code"] == -32602
    assert "secret" not in json.dumps(responses)


def test_eof_without_initialize_is_normal_transport_exit():
    code, responses, diagnostics = serve([])
    assert code == 0 and responses == []
    assert json.loads(diagnostics)["ownership_status"] == "unknown"


def test_ping_after_initialize():
    code, responses, _ = serve([init(), request("ping")])
    assert code == 0 and responses[-1]["result"] == {}


def test_collector_exception_is_not_leaked():
    def failed():
        raise RuntimeError("secret")

    code, responses, _ = serve(
        [init(), request("tools/call", {"name": "stdio_owner_observation"})],
        collector=failed,
    )
    assert code == 0
    assert responses[-1]["error"]["code"] == -32603
    assert "secret" not in json.dumps(responses)
