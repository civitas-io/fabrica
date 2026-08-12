"""SubprocessSandbox -- the Tier 0 Sandbox backend (isolation.md: "subprocess,
~0ms... trusted code, local dev").

Real subprocess boundary + a real ZMQ ipc:// callback bridge for
on_tool_call, matching system-design.md §3's design and the mechanism
validated in SPIKE-zmq-sandbox-channel-feasibility.md -- not a shortcut
substitute, since the whole point of that spike was to justify ZMQ
specifically for this.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import resource
import sys
import time
import uuid
from pathlib import Path
from typing import Any, cast

import zmq
import zmq.asyncio

from fabrica.sandbox.errors import SandboxCrashedError, SandboxTimeoutError
from fabrica.sandbox.types import MAX_STDOUT_BYTES, RunResult, SandboxHandle, ToolCallCallback

# Invoked as `python -m fabrica.sandbox._guest_shim`, NOT `python <path>` --
# a real bug found by actually running this, not a style preference.
# Running a script directly puts ITS OWN DIRECTORY on sys.path[0], and this
# package's own fabrica/sandbox/types.py then shadows Python's stdlib
# `types` module for anything imported afterward that needs it (enum,
# functools, dataclasses all break transitively). Module invocation avoids
# the whole class of "a submodule named types.py shadows stdlib" bugs.
_SHIM_MODULE = "fabrica.sandbox._guest_shim"


class SubprocessSandbox:
    """Implements the Sandbox protocol. No persistent state between
    boot_clean() and execute() -- Tier 0's whole point is near-zero boot
    cost, so each execute() spawns a fresh subprocess rather than reusing
    a pre-warmed one.
    """

    def __init__(self) -> None:
        # Deliberately /tmp directly, not tempfile.mkdtemp(): macOS's real
        # tmpdir (`$TMPDIR`, under /var/folders/...) is long enough that a
        # UUID-based socket filename exceeds sockaddr_un's 103-character
        # limit for ipc:// paths -- a real bug caught by actually running
        # this, not a theoretical concern. /tmp is short and available on
        # every platform this backend targets (Linux, macOS).
        self._socket_dir = Path("/tmp") / f"fabrica-sbx-{uuid.uuid4().hex[:8]}"
        self._socket_dir.mkdir(parents=True, exist_ok=True)

    async def boot_clean(self) -> SandboxHandle:
        # Short id, not a full UUID -- same ipc:// path-length constraint;
        # 8 hex chars is more than enough entropy within one process's
        # own socket directory.
        return SandboxHandle(id=uuid.uuid4().hex[:8], tier=0)

    async def execute(
        self,
        handle: SandboxHandle,
        code: str,
        *,
        on_tool_call: ToolCallCallback,
        timeout: float,
    ) -> RunResult:
        ipc_path = self._socket_dir / f"{handle.id}.sock"
        ipc_addr = f"ipc://{ipc_path}"

        ctx = zmq.asyncio.Context()
        rep_socket = ctx.socket(zmq.REP)
        rep_socket.bind(ipc_addr)

        start_cpu = resource.getrusage(resource.RUSAGE_CHILDREN)
        start_time = time.monotonic()

        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            _SHIM_MODULE,
            ipc_addr,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def _serve_tool_calls() -> None:
            while True:
                request = await rep_socket.recv_json()
                result = await on_tool_call(request["tool"], request["params"])
                await rep_socket.send_json(result)

        serve_task = asyncio.ensure_future(_serve_tool_calls())

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(code.encode()), timeout=timeout
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise SandboxTimeoutError(
                f"sandbox {handle.id} did not complete within {timeout}s"
            ) from None
        finally:
            serve_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await serve_task
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
                f"sandbox {handle.id} exited (code={proc.returncode}) without "
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

    async def terminate(self, handle: SandboxHandle) -> None:
        # Tier 0 has no persistent instance to tear down -- execute() is
        # already fully self-contained per call. Clean up any leftover
        # socket file defensively (e.g. if execute() was never called
        # after boot_clean(), or crashed before its own cleanup ran).
        (self._socket_dir / f"{handle.id}.sock").unlink(missing_ok=True)

    async def health_check(self) -> bool:
        return (Path(__file__).parent / "_guest_shim.py").exists()


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
    if not isinstance(trailer, dict) or not {
        "success",
        "error_message",
        "tool_call_count",
    } <= trailer.keys():
        return None
    return cast("dict[str, Any]", trailer)
