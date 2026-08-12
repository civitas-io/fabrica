"""MCPClient -- see docs/contracts/mcp-integration.md.

Migrates civitas-contrib/packages/fabrica/src/fabrica/mcp/client.py's real,
working connection-management logic (stdio_client/sse_client transport
selection, ClientSession lifecycle) -- not a rewrite of internals that
already worked. Two real things ARE changed, both corrections found by
reading the currently-installed `mcp` package's actual source, not by
transcribing the migrated code unchanged:

1. Attribute access is snake_case in the real, current `mcp` SDK
   (tool.input_schema, result.is_error) -- the migrated code used
   camelCase (tool.inputSchema, result.isError), matching an older SDK
   version. Confirmed directly by constructing real mcp.types.Tool/
   CallToolResult objects and reading their attributes.
2. BubblewrapSandbox -> SrtIsolation (fabrica/mcp/isolation.py) -- Linux-only
   -> cross-platform, per mcp-integration.md's resolved isolation mechanism.

AuditSink here is a minimal, LOCAL structural Protocol, not an import of
civitas.audit.types -- the migrated code imported civitas's own audit
types directly; this package depends on shapes, not packages, everywhere
except CivitasBridge's deliberate GenServer exception (contracts/
civitas-bridge.md). Audit emission stays best-effort and optional: a
caller who wants civitas's own audit schema can implement this Protocol
against it without fabrica needing to import civitas here at all.
"""

from __future__ import annotations

import contextlib
from typing import Any, Protocol, runtime_checkable

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client

from fabrica.mcp.errors import MCPConnectionError, MCPServerUnavailableError, MCPToolError
from fabrica.mcp.isolation import SrtIsolation
from fabrica.mcp.types import MCPServerConfig, MCPToolSchema


@runtime_checkable
class AuditSink(Protocol):
    async def emit(self, event: str, details: dict[str, Any]) -> None: ...


class MCPClient:
    """Manages a single persistent MCP connection (one session per agent
    per server). One MCPClient per server per agent -- shared pooling
    across agents, if ever needed, is ToolManager's job, not this class's.
    """

    def __init__(
        self,
        config: MCPServerConfig,
        audit_sink: AuditSink | None = None,
        agent_name: str = "",
    ) -> None:
        self.config = config
        self._session: ClientSession | None = None
        self._exit_stack = contextlib.AsyncExitStack()
        self._audit_sink = audit_sink
        self._agent_name = agent_name

    async def connect(self) -> None:
        """Idempotent -- a second call while already connected is a no-op.

        Raises:
            MCPConnectionError: transport-level failure.
            IsolationUnavailableError: srt unavailable and
                sandbox.allow_unsandboxed is False.
            UnsupportedSandboxConfigurationError: sandbox.network="allow"
                was requested -- srt cannot honor it.
        """
        if self._session is not None:
            return

        try:
            if self.config.transport == "stdio":
                read, write = await self._exit_stack.enter_async_context(
                    stdio_client(self._stdio_params())
                )
            else:
                assert self.config.url is not None  # enforced by MCPServerConfig.__post_init__
                read, write = await self._exit_stack.enter_async_context(
                    sse_client(self.config.url)
                )
            session = await self._exit_stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
        except MCPConnectionError:
            raise
        except Exception as exc:  # noqa: BLE001 -- transport failures are heterogeneous by nature
            await self._exit_stack.aclose()
            raise MCPConnectionError(
                f"MCPClient {self.config.name!r} failed to connect: {exc}"
            ) from exc
        self._session = session

        if self._audit_sink is not None:
            await self._audit_sink.emit(
                "mcp.connect",
                {
                    "server": self.config.name,
                    "agent": self._agent_name,
                    "transport": self.config.transport,
                },
            )

    def _stdio_params(self) -> StdioServerParameters:
        assert self.config.command is not None  # enforced by MCPServerConfig.__post_init__
        cmd = self.config.command
        cmd_args = self.config.args

        if self.config.sandbox is not None and self.config.sandbox.enabled:
            isolation = SrtIsolation(self.config.sandbox)
            isolation.check_or_raise()
            cmd, cmd_args = isolation.wrap(cmd, cmd_args)

        return StdioServerParameters(command=cmd, args=cmd_args, env=self.config.env)

    async def disconnect(self) -> None:
        """Closes the session and underlying transport/subprocess."""
        await self._exit_stack.aclose()
        self._session = None

    async def list_tools(self) -> list[MCPToolSchema]:
        """
        Raises:
            MCPConnectionError: not connected, or the server errors.
        """
        self._require_connected()
        assert self._session is not None
        try:
            result = await self._session.list_tools()
        except Exception as exc:  # noqa: BLE001 -- server-side errors are heterogeneous
            raise MCPConnectionError(
                f"MCPClient {self.config.name!r}: list_tools failed: {exc}"
            ) from exc
        return [
            MCPToolSchema(
                name=tool.name,
                description=tool.description or "",
                input_schema=dict(tool.input_schema) if tool.input_schema else {},
            )
            for tool in result.tools
        ]

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """
        Raises:
            MCPToolError: the server reports is_error=True.
            MCPServerUnavailableError: the connection is dead.
        """
        self._require_connected(unavailable=True)
        assert self._session is not None
        try:
            result = await self._session.call_tool(tool_name, arguments)
        except Exception as exc:  # noqa: BLE001 -- transport-level failure mid-call
            raise MCPServerUnavailableError(
                f"MCPClient {self.config.name!r}: call_tool({tool_name!r}) failed: {exc}"
            ) from exc

        if result.is_error:
            texts = [item.text for item in result.content if hasattr(item, "text")]
            detail = " ".join(texts) if texts else str(result.content)
            raise MCPToolError(tool_name, detail)

        texts = [item.text for item in result.content if hasattr(item, "text")]
        if texts and len(texts) == len(result.content):
            return "\n".join(texts)
        return result.content

    def _require_connected(self, *, unavailable: bool = False) -> None:
        if self._session is None:
            if unavailable:
                raise MCPServerUnavailableError(f"MCPClient {self.config.name!r} is not connected.")
            raise MCPConnectionError(
                f"MCPClient {self.config.name!r} is not connected. Call connect() first."
            )
