"""MCPClient -- see docs/contracts/mcp-integration.md.

Connects to a REAL MCP server (tests/mcp/fixtures/echo_server.py, using the
real `mcp` library's server-side API) over a real stdio subprocess boundary
-- not a mock. srt-sandboxed tests use the real `srt` binary if it's on
PATH, skipped otherwise (an environment gap, not a code gap).
"""

from __future__ import annotations

import shutil
import sys

import pytest

from fabrica.mcp.client import MCPClient
from fabrica.mcp.errors import (
    MCPConnectionError,
    MCPServerUnavailableError,
    MCPToolError,
    UnsupportedSandboxConfigurationError,
)
from fabrica.mcp.types import FilesystemMount, MCPServerConfig, SandboxConfig

_ECHO_SERVER_ARGS = ["-m", "tests.mcp.fixtures.echo_server"]
_srt_available = shutil.which("srt") is not None


def _stdio_config(**overrides: object) -> MCPServerConfig:
    defaults: dict[str, object] = {
        "name": "echo",
        "transport": "stdio",
        "command": sys.executable,
        "args": _ECHO_SERVER_ARGS,
    }
    defaults.update(overrides)
    return MCPServerConfig(**defaults)  # type: ignore[arg-type]


class TestConnectAndListTools:
    async def test_connects_and_lists_real_tools(self) -> None:
        client = MCPClient(_stdio_config())
        await client.connect()
        try:
            schemas = await client.list_tools()
        finally:
            await client.disconnect()
        names = {s.name for s in schemas}
        assert names == {"add", "always_fails"}
        add_schema = next(s for s in schemas if s.name == "add")
        assert add_schema.input_schema["required"] == ["a", "b"]

    async def test_connect_is_idempotent(self) -> None:
        client = MCPClient(_stdio_config())
        await client.connect()
        try:
            await client.connect()  # must not raise, not open a second subprocess
            schemas = await client.list_tools()
            assert len(schemas) == 2
        finally:
            await client.disconnect()

    async def test_list_tools_before_connect_raises(self) -> None:
        client = MCPClient(_stdio_config())
        with pytest.raises(MCPConnectionError):
            await client.list_tools()

    async def test_bad_command_raises_mcp_connection_error(self) -> None:
        client = MCPClient(_stdio_config(command="/no/such/executable-xyz"))
        with pytest.raises(MCPConnectionError):
            await client.connect()


class TestCallTool:
    async def test_call_tool_returns_real_result(self) -> None:
        client = MCPClient(_stdio_config())
        await client.connect()
        try:
            result = await client.call_tool("add", {"a": 2, "b": 3})
        finally:
            await client.disconnect()
        assert result == "5"

    async def test_call_tool_raises_mcp_tool_error_on_is_error(self) -> None:
        client = MCPClient(_stdio_config())
        await client.connect()
        try:
            with pytest.raises(MCPToolError) as exc_info:
                await client.call_tool("always_fails", {})
        finally:
            await client.disconnect()
        assert exc_info.value.tool_name == "always_fails"

    async def test_call_tool_before_connect_raises_server_unavailable(self) -> None:
        client = MCPClient(_stdio_config())
        with pytest.raises(MCPServerUnavailableError):
            await client.call_tool("add", {"a": 1, "b": 1})

    async def test_call_tool_after_disconnect_raises_server_unavailable(self) -> None:
        client = MCPClient(_stdio_config())
        await client.connect()
        await client.disconnect()
        with pytest.raises(MCPServerUnavailableError):
            await client.call_tool("add", {"a": 1, "b": 1})


class TestSseTransport:
    def test_missing_url_raises_at_config_construction(self) -> None:
        with pytest.raises(ValueError, match="requires 'url'"):
            MCPServerConfig(name="x", transport="sse")

    def test_missing_command_raises_at_config_construction(self) -> None:
        with pytest.raises(ValueError, match="requires 'command'"):
            MCPServerConfig(name="x", transport="stdio")


@pytest.mark.skipif(not _srt_available, reason="srt not installed on PATH")
class TestSrtIsolation:
    """Real srt subprocess wrapping -- not mocked. Skipped in environments
    without srt on PATH, per the srt spike's own established discipline.
    """

    async def test_sandboxed_connection_still_works(self) -> None:
        config = _stdio_config(sandbox=SandboxConfig(enabled=True, network="deny"))
        client = MCPClient(config)
        await client.connect()
        try:
            result = await client.call_tool("add", {"a": 10, "b": 20})
        finally:
            await client.disconnect()
        assert result == "30"

    async def test_network_allow_raises_unsupported_before_connecting(self) -> None:
        config = _stdio_config(sandbox=SandboxConfig(enabled=True, network="allow"))
        client = MCPClient(config)
        with pytest.raises(UnsupportedSandboxConfigurationError):
            await client.connect()

    async def test_boots_with_an_rw_filesystem_mount_configured(self) -> None:
        # Honest scope note: this proves a sandboxed subprocess with an rw
        # mount configured boots and answers correctly -- it does NOT prove
        # the mount grants real write access, since echo_server.py's own
        # tool set (add/always_fails) never touches the filesystem. A
        # dedicated filesystem-touching MCP tool would be needed for that,
        # which is out of scope for this contract's fixed tool set.
        import tempfile

        write_dir = tempfile.mkdtemp()
        config = _stdio_config(
            sandbox=SandboxConfig(
                enabled=True,
                network="deny",
                filesystem=[FilesystemMount(path=write_dir, mode="rw")],
            )
        )
        client = MCPClient(config)
        await client.connect()
        try:
            # add doesn't touch the filesystem -- this test only proves the
            # sandboxed subprocess boots and answers correctly with a real
            # rw mount configured; a dedicated filesystem-touching MCP tool
            # would be needed to prove write access itself, which
            # tests/mcp/fixtures/echo_server.py deliberately doesn't add
            # (out of scope for this contract's own tool set).
            result = await client.call_tool("add", {"a": 1, "b": 1})
        finally:
            await client.disconnect()
        assert result == "2"
