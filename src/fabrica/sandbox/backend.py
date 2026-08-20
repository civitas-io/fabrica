"""The Sandbox protocol -- see docs/contracts/sandbox.md.

A single-tier backend. Implementations: SubprocessSandbox (Tier 0,
implemented), SrtSandbox (Tier 1, implemented -- wraps `srt`, real
OS-level network/filesystem enforcement, live-verified on macOS; Linux/
Windows untested), FirecrackerSandbox (Tier 2, self-hosted, implemented
-- real vsock callback bridge, validated on real hardware, see
SPIKE-firecracker-vsock-callback-bridge.md). GvisorSandbox (Tier 1,
Linux -- may be superseded by SrtSandbox once its own Linux support is
verified, avoiding a second implementation) and LibkrunSandbox (Tier 2,
macOS) remain not implemented. Never used directly outside Fabrica --
always wrapped by SandboxPool.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from fabrica.sandbox.types import RunResult, SandboxHandle, ToolCallCallback


@runtime_checkable
class Sandbox(Protocol):
    @property
    def tier(self) -> int:
        """0/1/2 -- isolation.md's capability levels (3, Kata, has no
        Fabrica backend and is out of scope). A plain int, not an enum:
        the only thing ever done with it is a `< 2` comparison
        (WeakIsolationError, contracts/mcp-server.md) -- an enum would add
        ceremony with no behavior this doesn't already have. Fixed per
        backend instance, never changes at runtime -- SandboxPool wraps
        exactly one backend for its whole lifetime (isolation.md's
        platform dispatch happens once, at CivitasBridge.build() time).
        """
        ...

    async def boot_clean(self) -> SandboxHandle:
        """Boot, or restore-from-snapshot, a fresh instance in a known
        clean state. This is the ONLY way an instance is created -- there
        is no reuse path in this Protocol at all.
        """
        ...

    async def execute(
        self,
        handle: SandboxHandle,
        code: str,
        *,
        on_tool_call: ToolCallCallback,
        timeout: float,
        tool_call_timeout: float | None = None,
    ) -> RunResult:
        """tool_call_timeout, when set, bounds each individual
        on_tool_call() invocation separately from `timeout`'s overall
        budget -- closes contracts/sandbox.md's open item 3. `None` (the
        default) preserves the original behavior exactly: only the
        overall `timeout` applies, no per-call bound. Raises
        SandboxToolCallTimeoutError (a SandboxTimeoutError subclass) when
        a single call exceeds it -- the instance is killed either way,
        same consequence as the overall timeout, just attributed more
        specifically.
        """
        ...

    async def terminate(self, handle: SandboxHandle) -> None:
        """Tear down a single SandboxHandle permanently. Called by
        SandboxPool on every release() -- never on a path that reuses the
        handle.
        """
        ...

    async def health_check(self) -> bool: ...

    async def close(self) -> None:
        """Tear down BACKEND-INSTANCE-level resources -- anything this
        object allocated once, in `__init__` or lazily on first use, and
        shares across every handle it has ever produced via `boot_clean()`.
        Distinct from `terminate()`, which is scoped to a single handle:
        `terminate()` runs on every `release()` while OTHER handles from
        the same backend instance may still be warm and in active use, so
        it is the wrong place to tear down anything shared. `close()` runs
        exactly once, at deployment shutdown, called by SandboxPool.close()
        after every warm handle has already been drained and terminated --
        by the time this runs, no handle from this instance is still live.

        A real, confirmed bug this exists to prevent, not a hypothetical:
        an early SrtSandbox (Tier 1) implementation created a fresh
        directory under `/tmp` per BACKEND INSTANCE (`__init__`), used to
        hold every handle's per-execution socket/settings files, and
        implemented `terminate()` to clean up only those per-handle files
        -- correct for `terminate()`'s own scope, but nothing anywhere
        ever removed the instance-level directory itself, since
        `SandboxPool.close()` at the time only called `terminate()` per
        handle. Confirmed by inspecting `/tmp` directly: hundreds of
        empty, never-reclaimed directories had already accumulated purely
        from normal dev/test iteration -- a real, silently-growing leak,
        not a theoretical one.

        Most backends have nothing instance-level to release and should
        implement this as a genuine no-op (`SubprocessSandbox`,
        `FirecrackerSandbox` -- both write directly into a shared,
        externally-owned directory rather than allocating one of their
        own). The rule for any NEW backend: if `__init__` allocates
        anything beyond reading configuration (a directory, an open
        connection, a subprocess, a temp file) that is not scoped to one
        `SandboxHandle`, that resource's teardown belongs here, not in
        `terminate()` -- and it must be safe to call even if no handle was
        ever produced (a backend constructed and then closed without ever
        being used).
        """
        ...
