"""MCPToolNamespace -- see docs/contracts/mcp-integration.md.

Resolves the contract's own flagged Open item 1: eager connection
(required by ToolManager.register()'s existing contract) needs `connect()`
-- a coroutine -- to run before the object is usable, but Python
constructors can't be async. Resolved with an async factory
(`await MCPToolNamespace.create(client)`), matching this codebase's own
existing pattern for the same shape (contracts/prompts.md's `load()`,
contracts/managers.md's `SkillManager.load()`) -- __init__ itself stays
synchronous and private-by-convention, never called directly.
"""

from __future__ import annotations

from typing import Any

from fabrica.mcp.client import MCPClient
from fabrica.mcp.errors import MCPServerUnavailableError, MCPToolError
from fabrica.mcp.types import MCPToolSchema, ToolResult, ToolSchema


class MCPToolNamespace:
    """Implements the existing, unchanged ToolNamespace protocol
    (fabrica.tools.namespace.ToolNamespace) -- ToolManager cannot tell
    this apart from a hand-written namespace.
    """

    def __init__(self, client: MCPClient, schemas: list[MCPToolSchema]) -> None:
        """Not the public construction path -- use MCPToolNamespace.create()
        instead, which performs the required connect()/list_tools() calls
        this constructor assumes have already happened.
        """
        self._client = client
        self._schemas: dict[str, ToolSchema] = {
            s.name: ToolSchema(name=s.name, description=s.description, input_schema=s.input_schema)
            for s in schemas
        }

    @classmethod
    async def create(cls, client: MCPClient) -> MCPToolNamespace:
        """Connects EAGERLY -- forced by ToolManager.register()'s existing
        contract, which indexes a namespace's tools into the shared
        Retriever at registration time. Calls client.connect() and
        client.list_tools() here, once, at namespace-creation time --
        stubs()/open()/list_schemas() below are served from this cached
        list, never re-fetched per call.
        """
        await client.connect()
        schemas = await client.list_tools()
        return cls(client, schemas)

    def stubs(self) -> str:
        return "\n".join(f"{name}: {schema.description}" for name, schema in self._schemas.items())

    def open(self, path: str) -> ToolSchema:
        """
        Raises:
            KeyError: path doesn't name a known tool -- consistent with a
                plain dict-like lookup, no new error type for this case.
        """
        return self._schemas[path]

    def list_schemas(self) -> list[ToolSchema]:
        return list(self._schemas.values())

    async def call(self, name: str, params: dict[str, Any]) -> ToolResult:
        """Proxies to client.call_tool(name, params). Catches
        MCPToolError/MCPServerUnavailableError internally and returns
        ToolResult(success=False, ...) rather than letting either
        propagate -- a degraded MCP server is a ToolResult-level outcome
        the model sees, not an exception that aborts the whole execution.
        """
        try:
            value = await self._client.call_tool(name, params)
        except MCPToolError as exc:
            return ToolResult(success=False, value=None, error_message=str(exc))
        except MCPServerUnavailableError as exc:
            return ToolResult(success=False, value=None, error_message=str(exc))
        return ToolResult(success=True, value=value, error_message=None)
