"""ToolNamespace -- see docs/tool-execution.md.

Tools exposed as an explorable code-API surface. Code-mode execution binds a
ToolNamespace into a Sandbox runtime; the generated code imports the
namespace and calls it.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from fabrica.tools.types import ToolResult, ToolSchema


@runtime_checkable
class ToolNamespace(Protocol):
    def stubs(self) -> str:
        """Progressive-disclosure listing -- a human/model-facing summary,
        not structured data. See list_schemas() for the enumeration
        surface ToolManager actually indexes from.
        """
        ...

    def open(self, path: str) -> ToolSchema:
        """Load one tool's full definition by name."""
        ...

    async def call(self, name: str, params: dict[str, Any]) -> ToolResult: ...

    def list_schemas(self) -> list[ToolSchema]:
        """Added while implementing ToolManager.register()
        (docs/tool-execution.md's changelog) -- the enumeration surface
        stubs() doesn't provide, used only for indexing into the shared
        Retriever, never for anything human/model-facing.
        """
        ...


@dataclass(frozen=True)
class _RegisteredTool:
    schema: ToolSchema
    fn: Any


class DictToolNamespace:
    """A concrete, real ToolNamespace backed by a plain dict of Python
    callables -- the simplest legitimate case: a developer registers
    functions directly, no MCP server or other external source involved.
    """

    def __init__(self, tools: dict[str, tuple[ToolSchema, Any]]) -> None:
        self._tools = {name: _RegisteredTool(schema, fn) for name, (schema, fn) in tools.items()}

    def stubs(self) -> str:
        return "\n".join(
            f"{name}: {tool.schema.description}" for name, tool in self._tools.items()
        )

    def open(self, path: str) -> ToolSchema:
        return self._tools[path].schema

    def list_schemas(self) -> list[ToolSchema]:
        return [tool.schema for tool in self._tools.values()]

    async def call(self, name: str, params: dict[str, Any]) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(success=False, value=None, error_message=f"unknown tool: {name}")
        try:
            if inspect.iscoroutinefunction(tool.fn):
                value = await tool.fn(**params)
            else:
                value = await asyncio.to_thread(tool.fn, **params)
            return ToolResult(success=True, value=value, error_message=None)
        except Exception as exc:
            return ToolResult(success=False, value=None, error_message=str(exc))
