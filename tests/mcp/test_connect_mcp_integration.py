"""Real, end-to-end reproduction of the reported bug and proof it's fixed:
civitas.process.AgentProcess.connect_mcp() has always tried
`from fabrica.mcp.tool import MCPTool`, which never existed -- every real
call raised ModuleNotFoundError immediately (reported 2026-08-25 by a
downstream project team against civitas 0.11.3 / fabrica-context 0.4.0 /
presidium 0.6.0, verified directly against source before agreeing).

Lives in fabrica's own test suite, not civitas core's: civitas core's dev
environment deliberately does not install fabrica (see civitas's own root
pyproject.toml mypy-override comment, "not installed in the core dev
env"), so this is the one real place a full round trip through the actual,
sanctioned `connect_mcp()` -> `self.tools` path can be exercised against a
real MCP server -- fabrica already has a real, hard dependency on civitas.
"""

from __future__ import annotations

import sys

from civitas.plugins.tools import ToolRegistry
from civitas.process import AgentProcess

from fabrica.mcp.types import MCPServerConfig

_CONFIG = MCPServerConfig(
    name="echo",
    transport="stdio",
    command=sys.executable,
    args=["-m", "tests.mcp.fixtures.echo_server"],
)


class TestConnectMcp:
    async def test_connect_mcp_registers_real_tools_into_self_tools(self) -> None:
        """The exact scenario the bug report describes: before this fix,
        this call raised ModuleNotFoundError unconditionally.
        """
        agent = AgentProcess("researcher")
        agent.tools = ToolRegistry()

        await agent.connect_mcp(_CONFIG)
        try:
            names = agent.tools.names()
        finally:
            await agent._mcp_clients["echo"].disconnect()

        assert set(names) == {"mcp://echo/add", "mcp://echo/always_fails"}

    async def test_registered_tool_executes_for_real(self) -> None:
        agent = AgentProcess("researcher")
        agent.tools = ToolRegistry()

        await agent.connect_mcp(_CONFIG)
        try:
            tool = agent.tools.get("mcp://echo/add")
            assert tool is not None
            result = await tool.execute(a=4, b=5)
        finally:
            await agent._mcp_clients["echo"].disconnect()

        assert result == "9"

    async def test_connect_mcp_is_idempotent_and_deregisters_the_prefix_first(self) -> None:
        agent = AgentProcess("researcher")
        agent.tools = ToolRegistry()

        await agent.connect_mcp(_CONFIG)
        try:
            await agent.connect_mcp(_CONFIG)  # must not raise ValueError on re-register
            assert set(agent.tools.names()) == {"mcp://echo/add", "mcp://echo/always_fails"}
        finally:
            await agent._mcp_clients["echo"].disconnect()
