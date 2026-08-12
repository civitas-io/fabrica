"""Runs INSIDE the Tier 0 subprocess -- not imported by anything else.

Reads user code from stdin, executes it with a `namespace` object injected
that proxies namespace.call(tool, params) back to the parent process over a
ZMQ REQ/REP round trip on the ipc:// path given as argv[1] -- the mechanism
validated in SPIKE-zmq-sandbox-channel-feasibility.md (1.6MB footprint,
0.73ms round trip) and specified in system-design.md §3.

Stdout carries ONLY the user code's own print() output, uncontaminated --
the final result (success/error_message/tool_call_count) is written as a
single JSON line to stderr as the LAST action, keeping the two streams
cleanly separated so a real crash (no trailer written) is unambiguously
distinguishable from a routine code-level failure (trailer present,
success=False).
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from typing import Any, cast

import zmq


def main() -> None:
    ipc_path = sys.argv[1]
    code = sys.stdin.read()

    ctx = zmq.Context()
    sock = ctx.socket(zmq.REQ)
    sock.connect(ipc_path)

    tool_call_count = 0

    class _Namespace:
        def call(self, tool: str, params: dict[str, Any]) -> dict[str, Any]:
            nonlocal tool_call_count
            tool_call_count += 1
            sock.send_json({"tool": tool, "params": params})
            return cast("dict[str, Any]", sock.recv_json())

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

    sys.stdout.write(stdout_buf.getvalue())
    sys.stdout.flush()

    trailer = {
        "success": success,
        "error_message": error_message,
        "tool_call_count": tool_call_count,
    }
    sys.stderr.write(json.dumps(trailer) + "\n")
    sys.stderr.flush()

    sock.close(linger=0)
    ctx.term()


if __name__ == "__main__":
    main()
