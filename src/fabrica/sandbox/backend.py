"""The Sandbox protocol -- see docs/contracts/sandbox.md.

A single-tier backend. Implementations: SubprocessSandbox (Tier 0--
implemented here), GvisorSandbox / SrtSandbox (Tier 1, not implemented),
FirecrackerSandbox / LibkrunSandbox (Tier 2, not implemented). Never used
directly outside Fabrica -- always wrapped by SandboxPool.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from fabrica.sandbox.types import RunResult, SandboxHandle, ToolCallCallback


@runtime_checkable
class Sandbox(Protocol):
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
    ) -> RunResult: ...

    async def terminate(self, handle: SandboxHandle) -> None:
        """Tear down an instance permanently. Called by SandboxPool on
        every release() -- never on a path that reuses the handle.
        """
        ...

    async def health_check(self) -> bool: ...
