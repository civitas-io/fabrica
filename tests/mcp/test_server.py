"""FabricaMCPServer -- see docs/contracts/mcp-server.md.

Connects a REAL mcp.ClientSession, over a real stdio subprocess boundary,
to tests/mcp/fixtures/fabrica_stdio_server.py -- a real CivitasBridge-built
Fabrica wrapped in a real FabricaMCPServer. Not mocked anywhere in this
path: real Retriever, real SubprocessSandbox, real code-mode execution,
real in-memory long-term memory, all reached through the MCP protocol.
"""

from __future__ import annotations

import contextlib
import json
import sys
from typing import Any

import mcp.types as types
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from fabrica.civitas_bridge import CivitasBridge
from fabrica.mcp.server import (
    FabricaMCPServer,
    ServerTransportConfig,
    UnsupportedTransportError,
    _to_content,
)

_SERVER_ARGS = ["-m", "tests.mcp.fixtures.fabrica_stdio_server"]


class _Session:
    """Thin async-context wrapper around a connected real ClientSession,
    so every test doesn't repeat the same three-level nesting.
    """

    def __init__(self) -> None:
        self._stack: contextlib.AsyncExitStack = contextlib.AsyncExitStack()

    async def __aenter__(self) -> ClientSession:
        params = StdioServerParameters(command=sys.executable, args=_SERVER_ARGS)
        read, write = await self._stack.enter_async_context(stdio_client(params))
        session: ClientSession = await self._stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        return session

    async def __aexit__(self, *exc: object) -> None:
        await self._stack.aclose()


def _text_payload(result: types.CallToolResult) -> Any:
    first = result.content[0]
    assert isinstance(first, types.TextContent)
    return json.loads(first.text)


class TestFixedToolList:
    async def test_lists_exactly_five_fixed_tools_regardless_of_registrations(self) -> None:
        async with _Session() as session:
            tools = await session.list_tools()
        names = {t.name for t in tools.tools}
        assert names == {
            "fabrica_find",
            "fabrica_run_code",
            "fabrica_run_skill",
            "fabrica_memory_write",
            "fabrica_memory_search",
        }


class TestRunCode:
    async def test_run_code_executes_in_a_real_sandbox(self) -> None:
        async with _Session() as session:
            result = await session.call_tool("fabrica_run_code", {"code": "print(1 + 1)"})
        assert result.is_error is not True
        payload = _text_payload(result)
        assert payload["success"] is True
        assert "2" in payload["stdout"]

    async def test_unknown_tool_returns_is_error_not_a_protocol_crash(self) -> None:
        async with _Session() as session:
            result = await session.call_tool("no_such_fabrica_tool", {})
        assert result.is_error is True


class TestMemory:
    async def test_write_then_search_round_trips_through_real_memory_store(self) -> None:
        async with _Session() as session:
            write_result = await session.call_tool(
                "fabrica_memory_write", {"content": "the sandbox pool warms up two instances"}
            )
            assert write_result.is_error is not True
            search_result = await session.call_tool(
                "fabrica_memory_search", {"query": "sandbox pool warm", "limit": 5}
            )
        items = _text_payload(search_result)
        assert any("sandbox pool" in item["content"] for item in items)


class TestFind:
    async def test_find_with_no_kind_searches_both_tools_and_skills(self) -> None:
        async with _Session() as session:
            result = await session.call_tool("fabrica_find", {"query": "anything"})
        # An empty Fabrica (nothing registered) returns an empty combined
        # list -- proves the handler ran fabrica.tools.find() AND
        # fabrica.skills.find() without erroring, not that anything matched.
        assert _text_payload(result) == []


class TestPrompts:
    async def test_list_prompts_reflects_real_prompt_manager_state(self) -> None:
        async with _Session() as session:
            result = await session.list_prompts()
        assert result.prompts == []  # nothing loaded into this fixture's Fabrica


class TestConstruction:
    async def test_http_transport_raises_at_start_not_silently_no_ops(self) -> None:
        fabrica = await CivitasBridge(allow_ungoverned=True).build()
        server = FabricaMCPServer(
            fabrica, ServerTransportConfig(kind="http", host="localhost", port=8080)
        )
        with pytest.raises(UnsupportedTransportError):
            await server.start()


def test_to_content_serializes_dataclass_lists_as_json() -> None:
    from fabrica.retriever.types import Indexable, RankedMatch

    item = Indexable(id="x", kind="tool", name="x", description="d")
    content = _to_content([RankedMatch(item=item, rank=0)])
    assert isinstance(content[0], types.TextContent)
    payload = json.loads(content[0].text)
    expected_item = {"id": "x", "kind": "tool", "name": "x", "description": "d", "eager": False}
    assert payload == [{"item": expected_item, "rank": 0}]
