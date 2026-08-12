"""Types for ToolNamespace -- see docs/tool-execution.md."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolSchema:
    name: str
    description: str
    input_schema: dict[str, Any]
    eager: bool = False
    """Author-declared, per-item -- resolves retrieval.md's open item 2
    (per-deployment vs. per-item) in favor of per-item: a tool's own
    author is who actually knows whether it's critical/frequently-used
    enough to stay always-visible (Retriever.list_eager()), not an
    operator configuring an unrelated deployment. Same author-declares,
    harness-carries-it-through-without-validating shape as
    PromptTemplate.cacheable (contracts/prompts.md). A per-deployment
    override is a real, deferred idea (not built) -- see retrieval.md's
    resolution note.
    """


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
