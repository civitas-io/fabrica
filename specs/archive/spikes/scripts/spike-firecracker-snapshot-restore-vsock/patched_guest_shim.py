"""TEST PATCH for the snapshot/restore spike -- adds real reconnect logic
around the initial connect+ready+wait-for-code sequence, to test whether
a restore-induced ConnectionResetError is actually recoverable, not a
fundamental dead end. NOT the real shim -- proves the fix works; the real
fix belongs in src/fabrica/sandbox/_firecracker_guest_shim.py as its own,
separate, tested implementation task (PLAN.md item 20a), not this file.

Baked into a rootfs via:
    sudo mount -o loop <rootfs-copy> /mnt/fcrootfs
    sudo cp patched_guest_shim.py /mnt/fcrootfs/tmp/guest_shim.py
    sudo umount /mnt/fcrootfs
"""

from __future__ import annotations

import contextlib
import io
import json
import socket
import struct
import time
from typing import Any

_HOST_CID = 2
_HOST_PORT = 5555


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


def _connect_send_ready_and_wait_for_code() -> tuple[socket.socket, dict[str, Any]]:
    """The real fix, proven here: if a restore happens while blocked
    here, the OS delivers a real ConnectionResetError/OSError -- catch
    it, open a FRESH vsock socket, and try the whole ready/wait sequence
    again against whatever's now listening on the host side.
    """
    attempt = 0
    while True:
        attempt += 1
        sock = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)  # type: ignore[attr-defined]
        try:
            sock.connect((_HOST_CID, _HOST_PORT))
            _send(sock, {"type": "ready", "reconnect_attempt": attempt})
            code_message = _recv(sock)
            return sock, code_message
        except OSError as exc:
            with contextlib.suppress(Exception):
                sock.close()
            with open("/tmp/reconnect.log", "a") as f:
                f.write(f"attempt {attempt} failed: {type(exc).__name__}: {exc}\n")
            time.sleep(0.2)


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

    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
