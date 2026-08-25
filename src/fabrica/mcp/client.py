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

Real, third bug found 2026-08-25 while fixing AgentProcess.connect_mcp()'s
missing MCPTool, corrected in the same pass: AuditSink here USED TO BE a
minimal, LOCAL structural Protocol (`emit(event: str, details: dict)`),
deliberately not importing civitas.audit.types -- reasoned as "this package
depends on shapes, not packages, everywhere except CivitasBridge's
deliberate GenServer exception." That reasoning didn't hold up in practice:
connect_mcp() is the one real, current caller of this class, and it passes
a real civitas.audit.types.AuditSink instance straight through
(`audit_sink=self._audit_sink`) -- whose real `emit()` takes ONE argument
(a whole AuditEvent TypedDict: event/ts/agent/signer_id/details), not two
positional (event, details) arguments. Any agent with real auditing
configured would have hit a genuine `TypeError` the moment `connect()`
tried to emit its first "mcp.connect" event -- confirmed by reading
civitas.audit.types.AuditSink's real signature directly, not assumed.
Fixed by importing civitas's real AuditSink/AuditEvent directly, matching
architecture.md §1a's one deliberate "depend on packages, not shapes"
carve-out (already used for CivitasBridge) -- civitas is already this
package's real, hard runtime dependency, so this costs nothing new.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from typing import Any

from civitas.audit.types import AuditEvent, AuditSink
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from fabrica.mcp.errors import MCPConnectionError, MCPServerUnavailableError, MCPToolError
from fabrica.mcp.isolation import SrtIsolation
from fabrica.mcp.types import MCPServerConfig, MCPToolSchema

__all__ = ["AuditSink", "MCPClient"]


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
            elif self.config.transport == "streamable_http":
                assert self.config.url is not None  # enforced by __post_init__
                read, write = await self._exit_stack.enter_async_context(
                    streamable_http_client(self.config.url)
                )
            else:
                assert self.config.url is not None  # enforced by __post_init__
                read, write = await self._exit_stack.enter_async_context(
                    sse_client(self.config.url)
                )
            session = await self._exit_stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
        except MCPConnectionError:
            raise
        except asyncio.CancelledError as exc:
            # Real, confirmed finding, specific to streamable_http: a dead
            # endpoint's connection failure is raised by a SIBLING task
            # inside streamable_http_client's own internal anyio task
            # group, which cancels this method's single task rather than
            # raising a normal exception into it directly -- confirmed by
            # comparing against a bare, un-wrapped streamable_http_client
            # usage (which raises a clean, catchable ExceptionGroup) versus
            # this method's own AsyncExitStack-based connection lifecycle
            # (needed so the connection survives across separate connect()/
            # call_tool()/disconnect() calls, unlike a single contiguous
            # `async with` block). A CancelledError caught HERE always means
            # this specific, bounded connect() attempt did not complete --
            # not an ambient outer shutdown request this method should
            # silently swallow -- so converting it into MCPConnectionError
            # is correct, not a workaround that hides a real cancellation.
            #
            # The real, meaningful diagnosis (e.g. a genuine ConnectError)
            # only surfaces now, from aclose() itself, not from the
            # CancelledError above -- confirmed directly. Prefer it as the
            # cause when present; fall back to the CancelledError otherwise.
            cleanup_exc: BaseException = exc
            try:
                await self._exit_stack.aclose()
            except Exception as aclose_exc:  # noqa: BLE001 -- see comment above
                cleanup_exc = aclose_exc
            raise MCPConnectionError(
                f"MCPClient {self.config.name!r} failed to connect: {cleanup_exc}"
            ) from cleanup_exc
        except Exception as exc:  # noqa: BLE001 -- transport failures are heterogeneous by nature
            await self._exit_stack.aclose()
            raise MCPConnectionError(
                f"MCPClient {self.config.name!r} failed to connect: {exc}"
            ) from exc
        self._session = session

        if self._audit_sink is not None:
            await self._audit_sink.emit(
                AuditEvent(
                    event="mcp.connect",
                    ts=datetime.now(UTC).isoformat(),
                    agent=self._agent_name,
                    signer_id="",
                    details={
                        "server": self.config.name,
                        "transport": self.config.transport,
                    },
                )
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
