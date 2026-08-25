"""MCPTool -- real MCPClient connected to the real echo_server.py fixture
throughout, same discipline as test_namespace.py. Proves the
civitas.plugins.tools.ToolProvider shape civitas.process.AgentProcess.
connect_mcp() has always expected but could never import.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest
from civitas.audit.types import AuditEvent

from fabrica.mcp.client import MCPClient
from fabrica.mcp.errors import MCPToolError
from fabrica.mcp.tool import MCPTool
from fabrica.mcp.types import MCPServerConfig

_CONFIG = MCPServerConfig(
    name="echo",
    transport="stdio",
    command=sys.executable,
    args=["-m", "tests.mcp.fixtures.echo_server"],
)


class _RecordingSpan:
    def __init__(self, name: str, attributes: dict[str, Any]) -> None:
        self.name = name
        self.attributes = dict(attributes)
        self.error: BaseException | None = None
        self.ended = False

    def set_error(self, exc: BaseException) -> None:
        self.error = exc

    def end(self) -> None:
        self.ended = True


class _RecordingTracer:
    """Fast, in-memory test double -- same pattern as
    tests/test_observability.py's own _RecordingTracer, kept local here
    since that one is file-private, not a shared fixture.
    """

    def __init__(self) -> None:
        self.spans: list[_RecordingSpan] = []

    def start_span(
        self,
        name: str,
        trace_id: str = "",
        parent_span_id: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> _RecordingSpan:
        span = _RecordingSpan(name, attributes or {})
        self.spans.append(span)
        return span


class _RecordingAuditSink:
    """Matches civitas.audit.types.AuditSink's REAL shape (emit takes one
    AuditEvent, plus flush()/close()) -- the exact mismatch this bug fix
    corrects in fabrica.mcp.client.MCPClient.connect() too.
    """

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def emit(self, event: AuditEvent) -> None:
        self.events.append(event)

    async def flush(self) -> None:
        pass

    async def close(self) -> None:
        pass


async def _connected_client() -> MCPClient:
    client = MCPClient(_CONFIG)
    await client.connect()
    return client


class TestProtocolShape:
    async def test_satisfies_the_tool_provider_shape(self) -> None:
        client = await _connected_client()
        try:
            schemas = await client.list_tools()
            tool = MCPTool(client, next(s for s in schemas if s.name == "add"))
            assert hasattr(tool, "name")
            assert hasattr(tool, "schema")
            assert callable(tool.execute)
        finally:
            await client.disconnect()

    async def test_name_uses_the_documented_mcp_uri_scheme(self) -> None:
        client = await _connected_client()
        try:
            schemas = await client.list_tools()
            tool = MCPTool(client, next(s for s in schemas if s.name == "add"))
            assert tool.name == "mcp://echo/add"
        finally:
            await client.disconnect()

    async def test_schema_returns_the_input_schema_dict(self) -> None:
        client = await _connected_client()
        try:
            schemas = await client.list_tools()
            tool = MCPTool(client, next(s for s in schemas if s.name == "add"))
            assert tool.schema["required"] == ["a", "b"]
        finally:
            await client.disconnect()


class TestExecute:
    async def test_execute_returns_the_real_result(self) -> None:
        client = await _connected_client()
        try:
            schemas = await client.list_tools()
            tool = MCPTool(client, next(s for s in schemas if s.name == "add"))
            result = await tool.execute(a=4, b=5)
        finally:
            await client.disconnect()
        assert result == "9"

    async def test_execute_raises_real_mcp_tool_error_on_failure(self) -> None:
        """Unlike MCPToolNamespace.call() (catches into a ToolResult),
        ToolProvider.execute() has no result-wrapping convention -- the
        real exception must propagate, not be swallowed or translated
        into civitas.mcp.types.MCPToolError (confirmed dead)."""
        client = await _connected_client()
        try:
            schemas = await client.list_tools()
            tool = MCPTool(client, next(s for s in schemas if s.name == "always_fails"))
            with pytest.raises(MCPToolError):
                await tool.execute()
        finally:
            await client.disconnect()

    async def test_execute_emits_a_real_civitas_mcp_call_span(self) -> None:
        client = await _connected_client()
        tracer = _RecordingTracer()
        try:
            schemas = await client.list_tools()
            tool = MCPTool(
                client,
                next(s for s in schemas if s.name == "add"),
                tracer=tracer,  # type: ignore[arg-type]
                agent_name="researcher",
            )
            await tool.execute(a=1, b=2)
        finally:
            await client.disconnect()

        assert len(tracer.spans) == 1
        span = tracer.spans[0]
        assert span.name == "civitas.mcp.call"
        assert span.attributes["civitas.mcp.server"] == "echo"
        assert span.attributes["civitas.mcp.tool"] == "add"
        assert span.attributes["civitas.agent.name"] == "researcher"
        assert span.error is None
        assert span.ended is True

    async def test_execute_records_error_on_span_when_the_call_fails(self) -> None:
        client = await _connected_client()
        tracer = _RecordingTracer()
        try:
            schemas = await client.list_tools()
            tool = MCPTool(
                client,
                next(s for s in schemas if s.name == "always_fails"),
                tracer=tracer,  # type: ignore[arg-type]
            )
            with pytest.raises(MCPToolError):
                await tool.execute()
        finally:
            await client.disconnect()

        assert len(tracer.spans) == 1
        assert isinstance(tracer.spans[0].error, MCPToolError)
        assert tracer.spans[0].ended is True

    async def test_execute_emits_a_real_audit_event_on_success(self) -> None:
        client = await _connected_client()
        sink = _RecordingAuditSink()
        try:
            schemas = await client.list_tools()
            tool = MCPTool(
                client,
                next(s for s in schemas if s.name == "add"),
                audit_sink=sink,  # type: ignore[arg-type]
                agent_name="researcher",
            )
            await tool.execute(a=1, b=2)
        finally:
            await client.disconnect()

        assert len(sink.events) == 1
        event = sink.events[0]
        assert event["event"] == "mcp.tool.call"
        assert event["agent"] == "researcher"
        assert event["details"]["server"] == "echo"
        assert event["details"]["tool"] == "add"
        assert event["details"]["success"] is True

    async def test_execute_emits_a_real_audit_event_on_failure_too(self) -> None:
        client = await _connected_client()
        sink = _RecordingAuditSink()
        try:
            schemas = await client.list_tools()
            tool = MCPTool(
                client,
                next(s for s in schemas if s.name == "always_fails"),
                audit_sink=sink,  # type: ignore[arg-type]
            )
            with pytest.raises(MCPToolError):
                await tool.execute()
        finally:
            await client.disconnect()

        assert len(sink.events) == 1
        assert sink.events[0]["details"]["success"] is False
