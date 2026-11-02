"""Bounded, fail-closed activity observation for newline-delimited MCP stdio.

Only transient framing bytes and hashed, typed request IDs are retained. The
caller uses ``lock`` around admission and the final termination decision.
"""
from __future__ import annotations

import hashlib
import json
import threading
from typing import Literal

Direction = Literal["client", "server"]


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


class RpcActivityTracker:
    def __init__(self, *, max_frame_bytes: int = 1048576, max_pending: int = 4096):
        if max_frame_bytes <= 0 or max_pending <= 0:
            raise ValueError("tracking bounds must be positive")
        self.lock = threading.RLock()
        self.max_frame_bytes = max_frame_bytes
        self.max_pending = max_pending
        self._buffers = {"client": bytearray(), "server": bytearray()}
        self._pending = {"client": set(), "server": set()}
        self._seen = {"client": set(), "server": set()}
        self.unknown = False
        self.input_ended = False
        self.output_ended = False
        self.draining = False
        self.output_forwarding = 0
        self._deferred: list[tuple[str, bytes]] | None = None

    def mark_unknown(self) -> None:
        with self.lock:
            self.unknown = True
            for buffer in self._buffers.values():
                buffer.clear()

    @property
    def pending_count(self) -> int:
        with self.lock:
            return sum(len(values) for values in self._pending.values())

    @property
    def can_cleanup(self) -> bool:
        with self.lock:
            return (self.input_ended and not self.unknown
                    and not self.pending_count and not any(self._buffers.values())
                    and self.output_forwarding == 0)

    def begin_output(self, chunk: bytes) -> tuple[tuple[str, bytes], ...]:
        """Register output activity before I/O; defer response completion."""
        with self.lock:
            self.output_forwarding += 1
            self._deferred = []
            try:
                self.feed("server", chunk)
                return tuple(self._deferred)
            finally:
                self._deferred = None

    def finish_output(
        self, completions: tuple[tuple[str, bytes], ...], *, success: bool
    ) -> None:
        with self.lock:
            if not success:
                self.mark_unknown()
            elif not self.unknown:
                for origin, key in completions:
                    self._pending[origin].remove(key)
            self.output_forwarding -= 1

    def close_output(self) -> None:
        with self.lock:
            self.output_ended = True
            if self._buffers["server"]:
                self.mark_unknown()

    def close_input(self) -> None:
        with self.lock:
            self.input_ended = True
            if self._buffers["client"]:
                self.mark_unknown()

    def begin_drain(self) -> bool:
        with self.lock:
            if not self.can_cleanup:
                return False
            self.draining = True
            return True

    def feed(self, direction: Direction, chunk: bytes) -> None:
        with self.lock:
            if self.unknown:
                return
            if direction == "client" and self.input_ended and chunk:
                self.mark_unknown()
                return
            for piece in chunk.splitlines(keepends=True):
                buffer = self._buffers[direction]
                if len(buffer) + len(piece) > self.max_frame_bytes:
                    self.mark_unknown()
                    return
                buffer.extend(piece)
                if piece.endswith(b"\n"):
                    line = bytes(buffer)
                    buffer.clear()
                    try:
                        self._message(direction, json.loads(
                            line.decode("utf-8"), object_pairs_hook=_unique_object,
                            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
                        ))
                    except (ValueError, TypeError, RecursionError, OverflowError):
                        self.mark_unknown()
                        return

    def _message(self, direction: Direction, message: object) -> None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise ValueError("invalid message")
        has_id = "id" in message
        key = None
        if has_id:
            request_id = message["id"]
            if type(request_id) not in (int, str):
                raise ValueError("invalid ID")
            key = hashlib.sha256(json.dumps(
                [type(request_id).__name__, request_id], ensure_ascii=True
            ).encode("ascii")).digest()
        if "method" in message:
            if (not isinstance(message["method"], str) or not message["method"]
                    or "result" in message or "error" in message
                    or ("params" in message and not isinstance(message["params"], (dict, list)))):
                raise ValueError("invalid request")
            if has_id:
                if direction == "server" and (self.input_ended or self.draining):
                    raise ValueError("server request after admission closed")
                if (key in self._seen[direction]
                        or self.pending_count >= self.max_pending
                        or len(self._seen[direction]) >= self.max_pending * 16):
                    raise ValueError("duplicate or unbounded requests")
                self._pending[direction].add(key)
                self._seen[direction].add(key)
            return
        if not has_id or (("result" in message) == ("error" in message)):
            raise ValueError("invalid response")
        if "error" in message:
            error = message["error"]
            if (not isinstance(error, dict) or type(error.get("code")) is not int
                    or not isinstance(error.get("message"), str)):
                raise ValueError("invalid error")
        opposite = "server" if direction == "client" else "client"
        if key not in self._pending[opposite]:
            raise ValueError("unknown response")
        if self._deferred is not None:
            completion = (opposite, key)
            if completion in self._deferred:
                raise ValueError("duplicate response")
            self._deferred.append(completion)
        else:
            self._pending[opposite].remove(key)
