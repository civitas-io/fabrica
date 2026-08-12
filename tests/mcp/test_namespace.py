"""MCPToolNamespace -- see docs/contracts/mcp-integration.md.

Real MCPClient connected to the real echo_server.py fixture throughout --
proving MCPToolNamespace's ToolNamespace-protocol shape works end to end,
the same way ToolManager will actually use it.
"""

from __future__ import annotations

import sys

import pytest

from fabrica.mcp.client import MCPClient
from fabrica.mcp.namespace import MCPToolNamespace
from fabrica.mcp.types import MCPServerConfig
from fabrica.tools.namespace import ToolNamespace

_CONFIG = MCPServerConfig(
    name="echo",
    transport="stdio",
    command=sys.executable,
    args=["-m", "tests.mcp.fixtures.echo_server"],
)


async def _create_namespace() -> MCPToolNamespace:
    return await MCPToolNamespace.create(MCPClient(_CONFIG))


class TestCreate:
    async def test_create_connects_and_fetches_real_schemas(self) -> None:
        namespace = await _create_namespace()
        try:
            schemas = namespace.list_schemas()
        finally:
            await namespace._client.disconnect()
        assert {s.name for s in schemas} == {"add", "always_fails"}

    async def test_satisfies_the_tool_namespace_protocol(self) -> None:
        namespace = await _create_namespace()
        try:
            assert isinstance(namespace, ToolNamespace)
        finally:
            await namespace._client.disconnect()


class TestStubsAndOpen:
    async def test_stubs_lists_every_tool_with_its_description(self) -> None:
        namespace = await _create_namespace()
        try:
            stubs = namespace.stubs()
        finally:
            await namespace._client.disconnect()
        assert "add: Add two numbers" in stubs
        assert "always_fails: Always returns is_error=True" in stubs

    async def test_open_returns_the_full_schema(self) -> None:
        namespace = await _create_namespace()
        try:
            schema = namespace.open("add")
        finally:
            await namespace._client.disconnect()
        assert schema.name == "add"
        assert schema.input_schema["required"] == ["a", "b"]

    async def test_open_unknown_tool_raises_key_error(self) -> None:
        namespace = await _create_namespace()
        try:
            with pytest.raises(KeyError):
                namespace.open("no-such-tool")
        finally:
            await namespace._client.disconnect()


class TestCall:
    async def test_call_returns_success_tool_result(self) -> None:
        namespace = await _create_namespace()
        try:
            result = await namespace.call("add", {"a": 4, "b": 5})
        finally:
            await namespace._client.disconnect()
        assert result.success is True
        assert result.value == "9"
        assert result.error_message is None

    async def test_call_converts_mcp_tool_error_to_failed_tool_result(self) -> None:
        namespace = await _create_namespace()
        try:
            result = await namespace.call("always_fails", {})
        finally:
            await namespace._client.disconnect()
        assert result.success is False
        assert result.value is None
        assert "always_fails" in (result.error_message or "")

    async def test_call_after_disconnect_returns_failed_tool_result_not_exception(self) -> None:
        namespace = await _create_namespace()
        await namespace._client.disconnect()
        # MCPServerUnavailableError must be caught internally, per the
        # contract -- a degraded connection is a ToolResult-level outcome,
        # not an exception that aborts the whole sandboxed execution.
        result = await namespace.call("add", {"a": 1, "b": 1})
        assert result.success is False
        assert result.error_message is not None
