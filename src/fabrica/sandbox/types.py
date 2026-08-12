"""Types shared by Sandbox and SandboxPool -- see docs/contracts/sandbox.md."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

MAX_STDOUT_BYTES = 65536
"""64KB. Exceeding this truncates, sets stdout_truncated=True -- never
silently drops data without signaling it happened, never raises for a
chatty print() loop."""


@dataclass(frozen=True)
class SandboxHandle:
    """An opaque reference to one running instance. Callers must not depend
    on its internal fields -- they vary by backend/tier.
    """

    id: str
    tier: int


ToolCallCallback = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
"""Invoked once per namespace.call(tool, params) the running code makes.
The caller (ToolManager) supplies this; it is where the actual tool
execution and grant-checking happens, not inside Sandbox itself.
"""


@dataclass(frozen=True)
class RunResult:
    """The only thing that crosses back out of the sandbox boundary.

    Deliberately stdout-based, not a magic "return value" mechanism -- this
    matches exactly what SPIKE-code-mode-execution.md validated (the
    model's generated code prints its answer; that's what comes back), not
    an idealized structured-value extraction that was never tested.
    """

    success: bool
    stdout: str
    stdout_truncated: bool
    error_message: str | None  # populated iff success is False; the CODE's
    # own exception/traceback -- a routine outcome, not a raised SandboxError
    cpu_seconds: float
    duration_ms: float
    tool_call_count: int
