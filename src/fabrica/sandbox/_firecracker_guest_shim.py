"""Runs INSIDE a Firecracker guest as PID 1 (`init=`) -- not imported by
anything else, and NOT the same shape as `_guest_shim.py` (Tier 0's
subprocess-stdin/stdout-based protocol doesn't exist here -- there is no
shared stdin/stdout channel between host and a whole guest VM the way
there is with a real child process).

Validated by SPIKE-firecracker-vsock-callback-bridge.md: a real,
bidirectional AF_VSOCK round trip between a Firecracker guest and the
host, on real hardware. This shim is the real production protocol built
on that proven transport -- one persistent vsock connection, opened once
at boot, carrying a small sequence of length-prefixed JSON messages:

    guest -> host: {"type": "ready"}
    host  -> guest: {"type": "code", "code": "<python source>"}
    guest -> host: {"type": "tool_call", "tool": ..., "params": ...}   (0+ times)
    host  -> guest: {"type": "tool_result", "success": ..., "value": ...,
                     "error_message": ...}
    guest -> host: {"type": "result", "success": ..., "stdout": ...,
                    "error_message": ..., "tool_call_count": ...}

Length-prefixed (4-byte big-endian length + JSON body) rather than
newline-delimited -- code/output can legitimately contain literal
newlines; a length prefix has no such ambiguity.

Real reconnect logic around the initial connect/ready/wait-for-code
sequence, closing SPIKE-firecracker-snapshot-restore-vsock-
combination.md's own finding: a guest resumed from a snapshot while
blocked in this exact sequence gets a real, correct `ConnectionResetError`
(its old peer genuinely no longer exists once restored into a fresh
process) -- verified on real hardware that catching it and reconnecting
with a fresh `AF_VSOCK` socket against whatever's now listening works,
not theorized. Without this, an unhandled exception here kills this
process -- which is PID 1 (`init=`) -- and Linux panics rather than
allow PID 1 to exit. Only THIS sequence needs it: it's the only point
where a real deployment snapshots (see `FirecrackerSandbox`'s own
`use_snapshot_restore` docstring) -- once code starts running, a restore
mid-tool-call is a genuinely different, not-yet-validated state (named
as real, separate future work, not silently assumed to be covered).
"""

from __future__ import annotations

import contextlib
import io
import json
import socket
import struct
import time
from typing import Any

_HOST_CID = 2  # VMADDR_CID_HOST -- fixed, per vsock's own addressing scheme
_HOST_PORT = 5555  # matches SPIKE-firecracker-vsock-callback-bridge.md's proven port


def _send(sock: socket.socket, message: dict[str, Any]) -> None:
    body = json.dumps(message).encode()
    sock.sendall(struct.pack(">I", len(body)) + body)


def _recv(sock: socket.socket) -> dict[str, Any]:
    length_bytes = _recv_exact(sock, 4)
    (length,) = struct.unpack(">I", length_bytes)
    body = _recv_exact(sock, length)
    result: dict[str, Any] = json.loads(body)
    return result


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    chunks = []
    remaining = n
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError("vsock connection closed mid-message")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


_MAX_RECONNECT_ATTEMPTS = 200
"""Bounded, not infinite -- a genuine misconfiguration (nothing ever
listening) must eventually surface as a real crash, not hang silently
forever pretending to retry. At ~5ms between attempts (see below),
200 attempts is ~1s of real budget -- generous relative to the restore
spike's own measured ~9ms snapshot-load time, but not unbounded."""


def _connect_send_ready_and_wait_for_code() -> tuple[socket.socket, dict[str, Any]]:
    """Real reconnect loop -- see module docstring for the exact failure
    this closes and why only this sequence needs it. A fresh `AF_VSOCK`
    socket is opened on every attempt, not reused -- the whole point is
    that the OLD one is genuinely dead once a restore has happened.
    """
    attempt = 0
    while True:
        attempt += 1
        # AF_VSOCK is Linux-only -- this shim only ever runs inside a real
        # Firecracker guest (Linux), never on the host doing local type
        # checking (which may be macOS, per this project's own dev
        # environment) -- socket.AF_VSOCK is genuinely absent from
        # typeshed's non-Linux stubs, not a real bug this ignore is hiding.
        sock = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)  # type: ignore[attr-defined]
        try:
            sock.connect((_HOST_CID, _HOST_PORT))
            _send(sock, {"type": "ready"})
            code_message = _recv(sock)
            return sock, code_message
        except OSError:
            with contextlib.suppress(Exception):
                sock.close()
            if attempt >= _MAX_RECONNECT_ATTEMPTS:
                raise
            # No sleep on purpose for the first several attempts -- a real
            # restore's fresh listener is already up BEFORE resume_vm is
            # requested (FirecrackerSandbox's own ordering), so the very
            # next attempt usually succeeds immediately; a small backoff
            # only matters if it doesn't, avoiding a tight spin loop.
            if attempt > 3:
                time.sleep(0.005)


def main() -> None:
    sock, code_message = _connect_send_ready_and_wait_for_code()
    code = code_message["code"]

    tool_call_count = 0

    class _Namespace:
        def call(self, tool: str, params: dict[str, Any]) -> dict[str, Any]:
            nonlocal tool_call_count
            tool_call_count += 1
            _send(sock, {"type": "tool_call", "tool": tool, "params": params})
            response = _recv(sock)
            return {
                "success": response["success"],
                "value": response["value"],
                "error_message": response["error_message"],
            }

    namespace = _Namespace()
    stdout_buf = io.StringIO()
    success = True
    error_message: str | None = None

    try:
        with contextlib.redirect_stdout(stdout_buf):
            exec(compile(code, "<sandboxed_code>", "exec"), {"namespace": namespace})
    except Exception as exc:
        success = False
        error_message = f"{type(exc).__name__}: {exc}"

    _send(
        sock,
        {
            "type": "result",
            "success": success,
            "stdout": stdout_buf.getvalue(),
            "error_message": error_message,
            "tool_call_count": tool_call_count,
        },
    )
    sock.close()

    # Deliberately does NOT self-terminate (no poweroff/sysrq here) --
    # matches SandboxPool's existing design exactly: the HOST always
    # controls termination (SubprocessSandbox.terminate() kills its
    # subprocess directly; FirecrackerSandbox.terminate() kills the
    # firecracker process directly the same way), never the guest
    # deciding its own lifetime. Avoids needing a second, separate,
    # unvalidated mechanism (guest self-shutdown) on top of the one this
    # shim's whole job is to prove -- the host already has full control
    # once it reads this "result" message off the vsock connection.
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
