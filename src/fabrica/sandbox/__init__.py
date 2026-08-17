"""Sandbox / SandboxPool -- isolated execution for tools-as-code / code-mode.

See docs/contracts/sandbox.md for the full contract this implements.
"""

from fabrica.sandbox.backend import Sandbox
from fabrica.sandbox.dispatch import select_sandbox_backend
from fabrica.sandbox.errors import (
    SandboxCrashedError,
    SandboxError,
    SandboxPoolExhaustedError,
    SandboxTimeoutError,
    SandboxToolCallTimeoutError,
)
from fabrica.sandbox.firecracker_backend import FirecrackerSandbox
from fabrica.sandbox.pool import SandboxPool
from fabrica.sandbox.subprocess_backend import SubprocessSandbox
from fabrica.sandbox.types import MAX_STDOUT_BYTES, RunResult, SandboxHandle, ToolCallCallback

__all__ = [
    "MAX_STDOUT_BYTES",
    "FirecrackerSandbox",
    "RunResult",
    "Sandbox",
    "SandboxCrashedError",
    "SandboxError",
    "SandboxHandle",
    "SandboxPool",
    "SandboxPoolExhaustedError",
    "SandboxTimeoutError",
    "SandboxToolCallTimeoutError",
    "SubprocessSandbox",
    "ToolCallCallback",
    "select_sandbox_backend",
]
