"""ToolManager -- see docs/contracts/managers.md."""

from __future__ import annotations

from typing import Any

from fabrica.managers.execute_in_sandbox import execute_in_sandbox
from fabrica.presidium import PresidiumClient
from fabrica.retriever import Indexable, RankedMatch, Retriever
from fabrica.sandbox import RunResult, SandboxPool
from fabrica.scope import Scope
from fabrica.tools import ToolNamespace


class ToolManager:
    def __init__(
        self,
        retriever: Retriever,
        sandbox_pool: SandboxPool,
        presidium_client: PresidiumClient,
    ) -> None:
        self._retriever = retriever
        self._sandbox_pool = sandbox_pool
        self._presidium_client = presidium_client
        # Maps tool name -> the namespace that owns it, built up across all
        # register() calls -- lets on_tool_call route a call to whichever
        # namespace actually registered that tool, without the caller
        # needing to know which namespace is which.
        self._tools_by_name: dict[str, ToolNamespace] = {}

    @property
    def tier(self) -> int:
        """Delegates to the underlying SandboxPool -- added specifically
        for FabricaMCPServer's WeakIsolationError check
        (contracts/mcp-server.md), which needs to know isolation strength
        without reaching into ToolManager's private SandboxPool directly.
        Read-only -- ToolManager never changes tier itself.
        """
        return self._sandbox_pool.tier

    async def register(self, namespace: ToolNamespace) -> None:
        """Registers every tool in namespace as Indexable(kind="tool") with
        the shared Retriever. Delegates idempotency to Retriever.register --
        re-registering an identical namespace is a no-op, not an error.
        """
        schemas = namespace.list_schemas()
        items = [
            Indexable(
                id=schema.name,
                kind="tool",
                name=schema.name,
                description=schema.description,
                eager=schema.eager,
            )
            for schema in schemas
        ]
        await self._retriever.register(items)
        for schema in schemas:
            self._tools_by_name[schema.name] = namespace

    async def find(self, query: str, *, limit: int = 5) -> list[RankedMatch]:
        """The find() fallback for hosts that can't run code-mode. Thin
        delegation -- no logic of its own beyond fixing kind="tool".
        """
        return await self._retriever.search(query, kind="tool", limit=limit)

    async def run_code(
        self, code: str, *, agent_id: str, scope: Scope, timeout: float = 30.0
    ) -> RunResult:
        """The code-mode headline path. on_tool_call is wired here to
        actually invoke the registered ToolNamespace's real functions --
        this is where "real tool access" is implemented, not inside
        execute_in_sandbox, which knows nothing about what a tool call
        actually does.
        """

        async def on_tool_call(tool: str, params: dict[str, Any]) -> dict[str, Any]:
            namespace = self._tools_by_name.get(tool)
            if namespace is None:
                return {"success": False, "value": None, "error_message": f"unknown tool: {tool}"}
            result = await namespace.call(tool, params)
            return {
                "success": result.success,
                "value": result.value,
                "error_message": result.error_message,
            }

        return await execute_in_sandbox(
            presidium_client=self._presidium_client,
            sandbox_pool=self._sandbox_pool,
            action="code_mode",
            agent_id=agent_id,
            scope=scope,
            code=code,
            on_tool_call=on_tool_call,
            timeout=timeout,
        )
