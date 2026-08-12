"""Types for ToolNamespace -- see docs/tool-execution.md."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolSchema:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    """The result of ONE tool call -- distinct from RunResult
    (fabrica.sandbox.types), which is the result of a WHOLE code-mode
    execution (stdout, tool_call_count, etc). A single execute_in_sandbox
    run may involve many ToolResults, each folded into on_tool_call's
    return value, before the run as a whole produces one RunResult.
    """

    success: bool
    value: Any | None
    error_message: str | None
