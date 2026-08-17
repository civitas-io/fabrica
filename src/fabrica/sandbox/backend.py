"""The Sandbox protocol -- see docs/contracts/sandbox.md.

A single-tier backend. Implementations: SubprocessSandbox (Tier 0,
implemented), FirecrackerSandbox (Tier 2, self-hosted, implemented --
real vsock callback bridge, validated on real hardware, see
SPIKE-firecracker-vsock-callback-bridge.md). GvisorSandbox / SrtSandbox
(Tier 1) and LibkrunSandbox (Tier 2, macOS) remain not implemented. Never
used directly outside Fabrica -- always wrapped by SandboxPool.
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
        """Tear down an instance permanently. Called by SandboxPool on
        every release() -- never on a path that reuses the handle.
        """
        ...

    async def health_check(self) -> bool: ...
