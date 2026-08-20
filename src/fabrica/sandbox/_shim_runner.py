"""Shared core for any Sandbox backend that runs the guest shim as a plain
OS subprocess wrapped in a possibly-empty command prefix -- extracted from
SubprocessSandbox (Tier 0) so SrtSandbox (Tier 1) reuses the exact same,
already-hardened ZMQ ipc:// callback bridge and cleanup/timeout-racing
logic verbatim, not a re-derived copy. See docs/contracts/sandbox.md.

The prefix is the ONLY thing that varies between backends: Tier 0 launches
`python -m fabrica.sandbox._guest_shim <ipc_addr>` directly; Tier 1
(SrtSandbox) launches `srt --settings <path> -- python -m
fabrica.sandbox._guest_shim <ipc_addr>` -- same guest shim, same ZMQ
bridge, the OS-level restriction wraps the outside of the exact same
subprocess invocation.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import resource
import sys
import time
from pathlib import Path
from typing import Any, cast

import zmq
import zmq.asyncio

from fabrica.sandbox.errors import (
    SandboxCrashedError,
    SandboxTimeoutError,
    SandboxToolCallTimeoutError,
)
from fabrica.sandbox.types import MAX_STDOUT_BYTES, RunResult, ToolCallCallback

# Invoked as `python -m fabrica.sandbox._guest_shim`, NOT `python <path>` --
# a real bug found by actually running this, not a style preference.
# Running a script directly puts ITS OWN DIRECTORY on sys.path[0], and this
# package's own fabrica/sandbox/types.py then shadows Python's stdlib
# `types` module for anything imported afterward that needs it (enum,
# functools, dataclasses all break transitively). Module invocation avoids
# the whole class of "a submodule named types.py shadows stdlib" bugs.
SHIM_MODULE = "fabrica.sandbox._guest_shim"


async def run_shimmed_subprocess(
    *,
    command_prefix: list[str],
    ipc_path: Path,
    sandbox_id: str,
    code: str,
    on_tool_call: ToolCallCallback,
    timeout: float,
    tool_call_timeout: float | None = None,
) -> RunResult:
    """Bind the ZMQ REP side at `ipc_path`, launch
    `command_prefix + [sys.executable, "-m", SHIM_MODULE, ipc_addr]`, serve
    tool calls until the child exits or `timeout` elapses. `command_prefix`
    is `[]` for a bare Tier 0 subprocess, or an OS-sandboxing wrapper's own
    argv (e.g. `["srt", "--settings", <path>, "--"]`) for Tier 1 -- the
    guest shim and ZMQ bridge underneath are identical either way.

    Does not create or clean up `ipc_path`'s parent directory, and does not
    write/remove any settings file a wrapper needed -- those are the
    caller's responsibility, matching each backend's own lifecycle (Tier 0
    has none; Tier 1's settings file is per-call and backend-owned).
    """
    ipc_addr = f"ipc://{ipc_path}"

    ctx = zmq.asyncio.Context()
    rep_socket = ctx.socket(zmq.REP)
    rep_socket.bind(ipc_addr)

    start_cpu = resource.getrusage(resource.RUSAGE_CHILDREN)
    start_time = time.monotonic()

    proc = await asyncio.create_subprocess_exec(
        *command_prefix,
        sys.executable,
        "-m",
        SHIM_MODULE,
        ipc_addr,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def _serve_tool_calls() -> None:
        while True:
            request = await rep_socket.recv_json()
            if tool_call_timeout is not None:
                try:
                    result = await asyncio.wait_for(
                        on_tool_call(request["tool"], request["params"]),
                        timeout=tool_call_timeout,
                    )
                except TimeoutError as exc:
                    # Complete the REP socket's strict request/reply
                    # cycle before raising -- the child is about to
                    # be killed and will never read this, but leaving
                    # the exchange mid-cycle is bad hygiene regardless
                    # of whether it was the actual cause of the real
                    # hang found below (it wasn't -- see the finally
                    # block's own note).
                    with contextlib.suppress(Exception):
                        await rep_socket.send_json(
                            {
                                "success": False,
                                "value": None,
                                "error_message": "tool call timed out",
                            }
                        )
                    raise SandboxToolCallTimeoutError(
                        f"sandbox {sandbox_id}: tool call {request['tool']!r} did "
                        f"not complete within {tool_call_timeout}s"
                    ) from exc
            else:
                result = await on_tool_call(request["tool"], request["params"])
            await rep_socket.send_json(result)

    # Real correction, closing contracts/sandbox.md open item 3: a
    # naive asyncio.wait_for(proc.communicate(...), timeout=timeout)
    # around ONLY the process communication, with serve_task running
    # as a fully separate fire-and-forget task, meant a hung/timed-out
    # tool call inside serve_task was never actually observed by this
    # method at all -- the process would sit blocked waiting for a
    # ZMQ reply that never arrives, silently consuming the FULL
    # `timeout` budget before the outer wait_for finally fired,
    # reporting a generic SandboxTimeoutError with no attribution to
    # the real cause. Racing both tasks with FIRST_COMPLETED lets a
    # tool-call timeout kill the process immediately and report a
    # SandboxToolCallTimeoutError, not wait out the rest of the
    # overall budget first.
    serve_task = asyncio.ensure_future(_serve_tool_calls())
    communicate_task = asyncio.ensure_future(proc.communicate(code.encode()))

    try:
        done, _pending = await asyncio.wait(
            {serve_task, communicate_task}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        if communicate_task in done:
            stdout_bytes, stderr_bytes = communicate_task.result()
        else:
            proc.kill()
            await proc.wait()
            if serve_task in done:
                exc = serve_task.exception()
                if exc is not None:
                    raise exc
            raise SandboxTimeoutError(f"sandbox {sandbox_id} did not complete within {timeout}s")
    finally:
        serve_task.cancel()
        communicate_task.cancel()
        # A REAL bug found here, not assumed away: awaiting a task
        # that already completed with an exception RE-RAISES that
        # exception -- suppressing only asyncio.CancelledError (not
        # ANY exception) meant a tool-call timeout's own
        # SandboxToolCallTimeoutError, already handled above, got
        # raised a SECOND time here, silently skipping every
        # statement below (rep_socket.close(), ctx.destroy(),
        # ipc_path.unlink()) without ever surfacing as a visible
        # error -- because the re-raised exception happened to be the
        # exact same type pytest.raises() in a caller's test was
        # already expecting, so the test itself passed while leaking
        # the zmq Context. That leaked Context's own __del__ later
        # hung indefinitely during a full GC pass at interpreter
        # shutdown (pytest's own gc_collect_harder(), confirmed via a
        # real py-spy-adjacent stack dump), not during the test
        # itself -- which is exactly why the test appeared to pass
        # before this fix.
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await serve_task
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await communicate_task
        rep_socket.close(linger=0)
        ctx.term()
        ipc_path.unlink(missing_ok=True)

    end_cpu = resource.getrusage(resource.RUSAGE_CHILDREN)
    duration_ms = (time.monotonic() - start_time) * 1000
    cpu_seconds = (end_cpu.ru_utime - start_cpu.ru_utime) + (
        end_cpu.ru_stime - start_cpu.ru_stime
    )

    trailer = _parse_trailer(stderr_bytes)
    if trailer is None:
        raise SandboxCrashedError(
            f"sandbox {sandbox_id} exited (code={proc.returncode}) without "
            f"reporting a result -- stderr: {stderr_bytes.decode(errors='replace')[:500]}"
        )

    stdout_truncated = len(stdout_bytes) > MAX_STDOUT_BYTES
    stdout_text = stdout_bytes[:MAX_STDOUT_BYTES].decode(errors="replace")

    return RunResult(
        success=trailer["success"],
        stdout=stdout_text,
        stdout_truncated=stdout_truncated,
        error_message=trailer["error_message"],
        cpu_seconds=cpu_seconds,
        duration_ms=duration_ms,
        tool_call_count=trailer["tool_call_count"],
    )


def _parse_trailer(stderr_bytes: bytes) -> dict[str, Any] | None:
    """The guest shim's last stderr line is a JSON result trailer. Missing
    or malformed -- the shim never got to report cleanly -- signals a real
    crash, not a routine code failure.
    """
    lines = [line for line in stderr_bytes.decode(errors="replace").splitlines() if line.strip()]
    if not lines:
        return None
    try:
        trailer = json.loads(lines[-1])
    except json.JSONDecodeError:
        return None
    if (
        not isinstance(trailer, dict)
        or not {
            "success",
            "error_message",
            "tool_call_count",
        }
        <= trailer.keys()
    ):
        return None
    return cast("dict[str, Any]", trailer)
