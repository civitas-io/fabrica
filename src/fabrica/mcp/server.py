"""FabricaMCPServer -- see docs/contracts/mcp-server.md.

Fabrica as an MCP server -- the complementary opposite of MCPClient/
MCPToolNamespace (Fabrica as an MCP client). Five fixed, generic MCP tools
regardless of how many ToolNamespace tools or skills are registered
underneath -- the whole point of this contract is that an external MCP
caller pays the SAME O(1) two-path cost internal models already pay, not
the O(N) schema-dump cost this platform exists to eliminate.

Built against mcp.server.lowlevel.Server's real, current API (callback
params passed into the constructor -- on_list_tools/on_call_tool/
on_list_prompts/on_get_prompt -- not a decorator-based FastMCP style some
older `mcp` SDK versions used). Confirmed directly by reading the
installed package's real signature, same "reconcile against real source"
discipline as CivitasRuntime/MCPClient.

Only stdio transport is implemented here. HTTP/SSE transport
(ServerTransportConfig(kind="http")) is a real, documented gap, not a
silent stub -- see the module-level "What this module deliberately does
not cover" note near the bottom.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

import mcp.types as types
from mcp.server import ServerRequestContext
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from fabrica.civitas_bridge import Fabrica
from fabrica.memory.types import MemoryItem
from fabrica.scope import Scope


@runtime_checkable
class TokenAuthenticator(Protocol):
    """Injected dependency -- resolves a bearer token to an agent_id, or
    None if invalid/unrecognized. FabricaMCPServer never stores or
    validates tokens itself.
    """

    async def authenticate(self, token: str) -> str | None: ...


@dataclass(frozen=True)
class ServerTransportConfig:
    kind: Literal["stdio", "http"]
    host: str | None = None
    port: int | None = None
    authenticator: TokenAuthenticator | None = None
    stdio_agent_id: str = "mcp-stdio-client"


class FabricaMCPServerError(Exception):
    """Base for FabricaMCPServer-specific errors."""


class WeakIsolationError(FabricaMCPServerError):
    """Raised at construction time when the underlying SandboxPool's tier
    is below Tier 2 and allow_weak_isolation_for_external_callers is
    False (the default). Fourth confirmed instance of the fail-closed-
    by-default, explicit-opt-in-to-bypass pattern.
    """


class AuthenticationError(FabricaMCPServerError):
    """Raised when an HTTP/SSE connection presents a token that
    authenticator.authenticate() resolves to None.
    """


class UnsupportedTransportError(FabricaMCPServerError):
    """Raised by start() for kind="http" -- not implemented in this pass.
    See the module docstring's "What this module deliberately does not
    cover" note. Not a silent no-op: an operator who configures http
    transport gets a clear, typed error at start() time, not a server
    that appears to run but never actually listens.
    """


def _asdict_if_dataclass(item: Any) -> Any:
    return (
        dataclasses.asdict(item)
        if dataclasses.is_dataclass(item) and not isinstance(item, type)
        else item
    )


def _to_content(value: Any) -> list[types.ContentBlock]:
    """Every tool handler's return value crosses the MCP boundary as JSON
    text content -- dataclasses.asdict() plus a str() fallback for
    non-JSON-native fields (datetime), not a bespoke per-type serializer.
    """
    if isinstance(value, list):
        payload: Any = [_asdict_if_dataclass(item) for item in value]
    else:
        payload = _asdict_if_dataclass(value)
    content: list[types.ContentBlock] = [
        types.TextContent(type="text", text=json.dumps(payload, default=str))
    ]
    return content


@dataclass
class _ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Any  # Callable[[str, dict[str, Any]], Awaitable[Any]]


class FabricaMCPServer:
    def __init__(
        self,
        fabrica: Fabrica,
        transport: ServerTransportConfig,
        *,
        allow_weak_isolation_for_external_callers: bool = False,
    ) -> None:
        """
        Raises:
            WeakIsolationError: fabrica's underlying SandboxPool tier is
                below Tier 2 and allow_weak_isolation_for_external_callers
                is False.

        NOTE on WeakIsolationError: SandboxPool does not currently expose
        its backend's tier as a queryable attribute (contracts/sandbox.md
        never specified one) -- there is nothing to check yet. This is a
        real, named gap, not a silent skip: see Open items below. Until a
        tier-query surface exists on SandboxPool, this check cannot run
        for real, so it is not performed -- allow_weak_isolation_for_
        external_callers is accepted and stored but currently has no
        effect. Wraps an ALREADY-BUILT Fabrica facade -- this is an
        additional front door, never a replacement for direct in-process
        use.
        """
        self._fabrica = fabrica
        self._transport = transport
        self._allow_weak_isolation_for_external_callers = allow_weak_isolation_for_external_callers
        self._tools = self._build_tool_specs()
        self._server: Server[None] = Server(
            "fabrica",
            on_list_tools=self._on_list_tools,
            on_call_tool=self._on_call_tool,
            on_list_prompts=self._on_list_prompts,
            on_get_prompt=self._on_get_prompt,
        )
        self._stop_event = asyncio.Event()

    def _build_tool_specs(self) -> list[_ToolSpec]:
        return [
            _ToolSpec(
                name="fabrica_find",
                description="Search registered tools and skills by free-text query.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "kind": {"type": "string", "enum": ["tool", "skill"]},
                    },
                    "required": ["query"],
                },
                handler=self._handle_find,
            ),
            _ToolSpec(
                name="fabrica_run_code",
                description=(
                    "Run code-mode: execute generated code against registered tools in a sandbox."
                ),
                input_schema={
                    "type": "object",
                    "properties": {"code": {"type": "string"}},
                    "required": ["code"],
                },
                handler=self._handle_run_code,
            ),
            _ToolSpec(
                name="fabrica_run_skill",
                description="Run a named, author-trusted skill script.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "args": {"type": "object"},
                    },
                    "required": ["name"],
                },
                handler=self._handle_run_skill,
            ),
            _ToolSpec(
                name="fabrica_memory_write",
                description="Write an item to long-term memory.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "metadata": {"type": "object"},
                    },
                    "required": ["content"],
                },
                handler=self._handle_memory_write,
            ),
            _ToolSpec(
                name="fabrica_memory_search",
                description="Search long-term memory by free-text query.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "default": 5},
                    },
                    "required": ["query"],
                },
                handler=self._handle_memory_search,
            ),
        ]

    # -- tool handlers -----------------------------------------------------
    # agent_id is resolved by the connection layer (stdio_agent_id, or a
    # future HTTP authenticator), never accepted from the caller's own
    # arguments -- an external MCP client cannot claim a different
    # agent_id than the one its connection resolved to.

    async def _handle_find(self, agent_id: str, **kwargs: Any) -> list[Any]:
        query = kwargs["query"]
        kind = kwargs.get("kind")
        if kind == "skill":
            return await self._fabrica.skills.find(query)
        if kind == "tool":
            return await self._fabrica.tools.find(query)
        tools = await self._fabrica.tools.find(query)
        skills = await self._fabrica.skills.find(query)
        return [*tools, *skills]

    async def _handle_run_code(self, agent_id: str, **kwargs: Any) -> Any:
        return await self._fabrica.tools.run_code(
            kwargs["code"], agent_id=agent_id, scope=Scope(agent_id=agent_id)
        )

    async def _handle_run_skill(self, agent_id: str, **kwargs: Any) -> Any:
        return await self._fabrica.skills.run(
            kwargs["name"],
            kwargs.get("args", {}),
            agent_id=agent_id,
            scope=Scope(agent_id=agent_id),
        )

    async def _handle_memory_write(self, agent_id: str, **kwargs: Any) -> str:
        item = MemoryItem(id=None, content=kwargs["content"], metadata=kwargs.get("metadata") or {})
        return await self._fabrica.memory.write(Scope(agent_id=agent_id), item)

    async def _handle_memory_search(self, agent_id: str, **kwargs: Any) -> list[MemoryItem]:
        return await self._fabrica.memory.search(
            Scope(agent_id=agent_id), kwargs["query"], kwargs.get("limit", 5)
        )

    # -- mcp.server.lowlevel.Server callback wiring -------------------------

    async def _on_list_tools(
        self, ctx: ServerRequestContext[None], params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(name=t.name, description=t.description, input_schema=t.input_schema)
                for t in self._tools
            ]
        )

    async def _on_call_tool(
        self, ctx: ServerRequestContext[None], params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        spec = next((t for t in self._tools if t.name == params.name), None)
        if spec is None:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"unknown tool: {params.name}")],
                is_error=True,
            )
        agent_id = self._transport.stdio_agent_id
        try:
            result = await spec.handler(agent_id, **(params.arguments or {}))
        except Exception as exc:  # noqa: BLE001 -- surfaced to the caller as is_error, not raised
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=str(exc))], is_error=True
            )
        return types.CallToolResult(content=_to_content(result))

    async def _on_list_prompts(
        self, ctx: ServerRequestContext[None], params: types.PaginatedRequestParams | None
    ) -> types.ListPromptsResult:
        names = await self._fabrica.prompts.list_names()
        prompts: list[types.Prompt] = []
        for name in names:
            template = await self._fabrica.prompts.get(name)
            if template is not None:
                prompts.append(types.Prompt(name=template.name, description=None))
        return types.ListPromptsResult(prompts=prompts)

    async def _on_get_prompt(
        self, ctx: ServerRequestContext[None], params: types.GetPromptRequestParams
    ) -> types.GetPromptResult:
        version = None
        if params.arguments and "version" in params.arguments:
            version = int(params.arguments["version"])
        template = await self._fabrica.prompts.get(params.name, version)
        if template is None:
            return types.GetPromptResult(description=f"no such prompt: {params.name}", messages=[])
        return types.GetPromptResult(
            description=template.metadata.get("description"),
            messages=[
                types.PromptMessage(
                    role="user", content=types.TextContent(type="text", text=template.content)
                )
            ],
        )

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Begins listening. Blocks until stop() is called or the process
        ends.

        Raises:
            UnsupportedTransportError: transport.kind == "http" -- not
                implemented in this pass.
        """
        if self._transport.kind == "http":
            raise UnsupportedTransportError(
                "ServerTransportConfig(kind='http') is not implemented yet -- "
                "see docs/contracts/mcp-server.md's implementation notes. "
                "Only kind='stdio' is real today."
            )
        self._stop_event = asyncio.Event()
        async with stdio_server() as (read_stream, write_stream):
            init_options = self._server.create_initialization_options()
            run_task = asyncio.ensure_future(
                self._server.run(read_stream, write_stream, init_options)
            )
            stop_task = asyncio.ensure_future(self._stop_event.wait())
            done, pending = await asyncio.wait(
                {run_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            for task in done:
                if task is run_task:
                    task.result()  # re-raise a real server-side failure, if any

    async def stop(self) -> None:
        """Signals start() to return. Closing the underlying stdio streams
        themselves is stdio_server()'s own responsibility, exited when
        start()'s `async with` block returns.
        """
        self._stop_event.set()


# What this module deliberately does not cover:
#
# - HTTP/SSE transport (ServerTransportConfig(kind="http")) -- real ASGI
#   app assembly (mcp.server.streamable_http/streamable_http_manager),
#   bearer-token extraction, and TokenAuthenticator wiring into a live
#   HTTP listener is a genuinely separate, larger unit of engineering
#   (a real running HTTP server, an ASGI framework choice, a dedicated
#   test harness) that this pass did not reach. start() raises
#   UnsupportedTransportError for it rather than silently no-op'ing.
# - WeakIsolationError's real tier check -- SandboxPool has no queryable
#   tier attribute yet (contracts/sandbox.md never specified one); until
#   it does, allow_weak_isolation_for_external_callers is accepted but
#   inert. See Open items in contracts/mcp-server.md.
