"""SubprocessSandbox -- the Tier 0 Sandbox backend (isolation.md: "subprocess,
~0ms... trusted code, local dev").

Real subprocess boundary + a real ZMQ ipc:// callback bridge for
on_tool_call, matching system-design.md §3's design and the mechanism
validated in SPIKE-zmq-sandbox-channel-feasibility.md -- not a shortcut
substitute, since the whole point of that spike was to justify ZMQ
specifically for this. The actual subprocess-launch-and-serve mechanics
live in _shim_runner.py, shared with SrtSandbox (Tier 1) -- this class
supplies an empty command prefix, since Tier 0 has no OS-level wrapper.

Deliberately provides NO network or filesystem isolation -- "trusted code,
local dev" per isolation.md's own tier description. A sandboxed process
here can make direct network connections (confirmed: _guest_shim.py execs
code with full Python builtins available, no import restriction). Never
select this tier for untrusted code or any action a scope document must
bound -- use SrtSandbox (Tier 1) or FirecrackerSandbox (Tier 2) instead.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fabrica.sandbox._shim_runner import run_shimmed_subprocess
from fabrica.sandbox.types import RunResult, SandboxHandle, ToolCallCallback


class SubprocessSandbox:
    """Implements the Sandbox protocol. No persistent state between
    boot_clean() and execute() -- Tier 0's whole point is near-zero boot
    cost, so each execute() spawns a fresh subprocess rather than reusing
    a pre-warmed one.
    """

    @property
    def tier(self) -> int:
        return 0

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
        tool_call_timeout: float | None = None,
    ) -> RunResult:
        ipc_path = self._socket_dir / f"{handle.id}.sock"
        return await run_shimmed_subprocess(
            command_prefix=[],
            ipc_path=ipc_path,
            sandbox_id=handle.id,
            code=code,
            on_tool_call=on_tool_call,
            timeout=timeout,
            tool_call_timeout=tool_call_timeout,
        )

    async def terminate(self, handle: SandboxHandle) -> None:
        # Tier 0 has no persistent instance to tear down -- execute() is
        # already fully self-contained per call. Clean up any leftover
        # socket file defensively (e.g. if execute() was never called
        # after boot_clean(), or crashed before its own cleanup ran).
        (self._socket_dir / f"{handle.id}.sock").unlink(missing_ok=True)

    async def health_check(self) -> bool:
        return (Path(__file__).parent / "_guest_shim.py").exists()

    async def close(self) -> None:
        """A genuine no-op: __init__ allocates nothing beyond a reference
        to a shared, externally-owned directory (self._socket_dir, which
        defaults to plain "/tmp" itself) -- there is no instance-level
        resource of this backend's own to release. See Sandbox.close()'s
        own docstring for why this method exists at all."""
