"""MCPTool -- closes a real, confirmed, currently-blocking bug in
civitas.process.AgentProcess.connect_mcp(): it has always tried
`from fabrica.mcp.tool import MCPTool`, and this module never existed --
every real call to connect_mcp() raised ModuleNotFoundError immediately
(reported 2026-08-25 by a downstream project team, verified directly
against source before agreeing).

connect_mcp() itself needed no redesign -- it already calls the real,
existing MCPClient.list_tools()/call_tool() correctly and constructs
`MCPTool(client, schema, tracer=..., audit_sink=..., agent_name=...)` once
per schema; that exact constructor shape was never free to choose here,
it had to match the real, already-shipped caller.

MCPTool is the OTHER real MCP integration shape, not a replacement for
MCPToolNamespace (fabrica.mcp.namespace) -- both wrap the same MCPClient,
for two legitimately different consumers: MCPToolNamespace is one object
per SERVER, multiplexing every tool through `.call(name, params)`, for
Fabrica's own code-mode/ToolManager path; MCPTool is one object per TOOL,
implementing civitas.plugins.tools.ToolProvider (`name`/`schema`/
`execute(**kwargs)`), for civitas.plugins.tools.ToolRegistry -- the shape
any agent NOT using Fabrica's context layer needs. Composes directly with
presidium.providers.civitas_adapters.GovernedToolAdapter, which was
already correctly built one-per-tool -- it had nothing real to wrap
before this.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from civitas.audit.types import AuditEvent, AuditSink
from civitas.observability.tracer import Tracer

from fabrica.mcp.client import MCPClient
from fabrica.mcp.types import MCPToolSchema


class MCPTool:
    """A real `civitas.plugins.tools.ToolProvider` wrapping one MCP tool.

    One instance per (server, tool) pair -- matches connect_mcp()'s own
    existing per-schema construction loop, one MCPTool per entry in
    `await client.list_tools()`.
    """

    def __init__(
        self,
        client: MCPClient,
        schema: MCPToolSchema,
        *,
        tracer: Tracer | None = None,
        audit_sink: AuditSink | None = None,
        agent_name: str = "",
    ) -> None:
        self._client = client
        self._schema = schema
        self._tracer = tracer
        self._audit_sink = audit_sink
        self._agent_name = agent_name

    @property
    def name(self) -> str:
        """``mcp://server_name/tool_name`` -- matches connect_mcp()'s own
        ``deregister_prefix(f"mcp://{config.name}/")`` call exactly, and
        docs/mcp.md's already-documented addressing scheme.
        """
        return f"mcp://{self._client.config.name}/{self._schema.name}"

    @property
    def schema(self) -> dict[str, Any]:
        return self._schema.input_schema

    async def execute(self, **kwargs: Any) -> Any:
        """Invoke the real MCP tool via the shared ``MCPClient``.

        Raises fabrica's own, real ``MCPToolError``/``MCPServerUnavailableError``
        on failure -- unlike ``MCPToolNamespace.call()`` (which catches these
        into a ``ToolResult``, since a degraded MCP server is a routine,
        model-visible outcome on the code-mode path), ``ToolProvider.execute()``
        has no result-wrapping convention of its own, so letting the real
        exception propagate is the honest choice. Not translated into
        ``civitas.mcp.types.MCPToolError`` -- confirmed dead (see that
        class's own docstring); an ``except`` on it would never fire anyway.
        """
        span = None
        if self._tracer is not None:
            span = self._tracer.start_span(
                "civitas.mcp.call",
                attributes={
                    "civitas.mcp.server": self._client.config.name,
                    "civitas.mcp.tool": self._schema.name,
                    "civitas.agent.name": self._agent_name,
                },
            )

        error: Exception | None = None
        try:
            return await self._client.call_tool(self._schema.name, kwargs)
        except Exception as exc:
            error = exc
            if span is not None:
                span.set_error(exc)
            raise
        finally:
            if span is not None:
                span.end()
            if self._audit_sink is not None:
                await self._audit_sink.emit(
                    AuditEvent(
                        event="mcp.tool.call",
                        ts=datetime.now(UTC).isoformat(),
                        agent=self._agent_name,
                        signer_id="",
                        details={
                            "server": self._client.config.name,
                            "tool": self._schema.name,
                            "success": error is None,
                        },
                    )
                )
