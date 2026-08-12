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

Both stdio and HTTP transports are real. HTTP reuses the `mcp` library's
own real bearer-auth support (`Server.streamable_http_app(token_verifier=...)`,
`mcp.server.auth`'s `AuthenticationMiddleware`/`BearerAuthBackend`/
`RequireAuthMiddleware`) rather than hand-rolled ASGI middleware --
confirmed working end to end (a real uvicorn server, a real
`mcp.client.streamable_http` client, real bearer-token accept/reject)
before wiring it in here, not assumed from the library's docs.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

import mcp.types as types
import uvicorn
from mcp.server import ServerRequestContext
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.provider import TokenVerifier as _TokenVerifierProtocol
from mcp.server.auth.settings import AuthSettings
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from pydantic import AnyHttpUrl

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

    def __post_init__(self) -> None:
        """Real gap found and fixed here, not just in the docstring: the
        contract states authenticator is "Required if kind == 'http'" but
        never enforced it. Enforced now, matching MCPServerConfig's own
        __post_init__ validation pattern (fabrica.mcp.types).
        """
        missing_http_fields = self.host is None or self.port is None or self.authenticator is None
        if self.kind == "http" and missing_http_fields:
            raise ValueError(
                "ServerTransportConfig(kind='http') requires host, port, and authenticator "
                "together."
            )


class _TokenVerifierAdapter(_TokenVerifierProtocol):
    """Wraps a TokenAuthenticator to satisfy mcp.server.auth.provider's
    real TokenVerifier Protocol -- reuses the mcp library's own bearer-auth
    middleware (AuthenticationMiddleware/BearerAuthBackend/
    RequireAuthMiddleware) instead of hand-rolling ASGI auth. Subclasses
    the real Protocol directly (not just structurally matching it) so
    mypy verifies verify_token()'s signature against it exactly. agent_id
    is carried in AccessToken.subject -- the one field OAuth's AccessToken
    shape has that maps onto "resolved caller identity" without overloading
    client_id (which means something different: the OAuth client
    application, not the end caller).
    """

    def __init__(self, authenticator: TokenAuthenticator) -> None:
        self._authenticator = authenticator

    async def verify_token(self, token: str) -> AccessToken | None:
        agent_id = await self._authenticator.authenticate(token)
        if agent_id is None:
            return None
        return AccessToken(token=token, client_id="fabrica-mcp-client", scopes=[], subject=agent_id)


class FabricaMCPServerError(Exception):
    """Base for FabricaMCPServer-specific errors."""


class WeakIsolationError(FabricaMCPServerError):
    """Raised at construction time when the underlying SandboxPool's tier
    is below Tier 2 and allow_weak_isolation_for_external_callers is
    False (the default). Fourth confirmed instance of the fail-closed-
    by-default, explicit-opt-in-to-bypass pattern.
    """


class AuthenticationError(FabricaMCPServerError):
    """NOT raised by this module directly -- authentication rejection is
    enforced by the real mcp.server.auth middleware
    (RequireAuthMiddleware), which returns a 401 HTTP response rather than
    raising a Python exception FabricaMCPServer could catch. Kept in this
    module's public surface per the contract, for a caller who wants to
    catch a single FabricaMCPServerError subclass hierarchy -- but nothing
    in src/fabrica/mcp/server.py constructs or raises it today.
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


_MIN_SAFE_TIER_FOR_EXTERNAL_CALLERS = 2
"""isolation.md's Tier 2 (Firecracker/libkrun -- hardware-grade isolation).
An external MCP caller is, by definition, less trusted than an in-process
model already running inside Fabrica's own deployment -- Tier 0/1 are
accepted defaults for internal code-mode, but exposing the same execution
path to arbitrary external callers without hardware-grade isolation is a
genuinely different risk, per mcp-server.md's resolved isolation rule.
"""


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

        Checked against fabrica.tools.tier -- ToolManager/SkillManager
        share the same SandboxPool (CivitasBridge.build() constructs one
        pool for both), so checking either would give the same answer;
        tools is checked since fabrica_run_code is the tool this matters
        most for. Real as of this contract's tier-query addition to
        contracts/sandbox.md -- previously this check could not run at
        all (SandboxPool exposed no queryable tier), and
        allow_weak_isolation_for_external_callers was accepted but inert.

        Honest consequence, worth stating plainly: only Tier 0
        (SubprocessSandbox) is actually implemented anywhere in this
        codebase today (contracts/sandbox.md) -- so, as of this writing,
        EVERY real FabricaMCPServer(kind="http") deployment must pass
        allow_weak_isolation_for_external_callers=True to construct at
        all. That is the fail-closed default working exactly as intended,
        not a bug -- it forces an explicit, greppable acknowledgment of a
        real, current limitation, rather than a false sense of safety.

        Wraps an ALREADY-BUILT Fabrica facade -- this is an additional
        front door, never a replacement for direct in-process use.
        """
        if (
            fabrica.tools.tier < _MIN_SAFE_TIER_FOR_EXTERNAL_CALLERS
            and not allow_weak_isolation_for_external_callers
        ):
            raise WeakIsolationError(
                f"fabrica's SandboxPool is Tier {fabrica.tools.tier}, below the "
                f"Tier {_MIN_SAFE_TIER_FOR_EXTERNAL_CALLERS} minimum recommended for "
                "external MCP callers. Set allow_weak_isolation_for_external_callers=True "
                "to run anyway (e.g. for local development, or before a Tier 2 backend "
                "is deployed)."
            )
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
        self._uvicorn_server: uvicorn.Server | None = None

    def _resolve_agent_id(self) -> str:
        """stdio: one trusted local caller per session, a single
        configured default agent_id suffices (contracts/mcp-server.md).
        http: resolved once per request by RequireAuthMiddleware +
        _TokenVerifierAdapter, retrieved via get_access_token()'s
        contextvar -- always present here, since RequireAuthMiddleware
        already rejected any request that didn't authenticate before this
        handler ever runs.
        """
        if self._transport.kind == "stdio":
            return self._transport.stdio_agent_id
        access_token = get_access_token()
        assert access_token is not None and access_token.subject is not None
        return access_token.subject

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
        agent_id = self._resolve_agent_id()
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
        """Begins listening (stdio: reads from stdin; http: binds
        host:port). Blocks until stop() is called or the process ends.
        """
        if self._transport.kind == "http":
            await self._start_http()
        else:
            await self._start_stdio()

    async def _start_stdio(self) -> None:
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

    async def _start_http(self) -> None:
        """Real ASGI app + real bearer-auth, both from the `mcp` library
        itself, not hand-rolled. AuthSettings.issuer_url/resource_server_url
        are both REQUIRED fields on the real pydantic model even though
        no real OAuth authorization-server flow is used here -- only
        token_verifier's bearer-token path is exercised (confirmed
        directly: streamable_http_app only adds OAuth discovery ROUTES
        when auth_server_provider is also set, which it never is here).
        issuer_url is a required-but-otherwise-unused placeholder in that
        configuration, not a real identity for this server to assert.
        """
        assert self._transport.host is not None
        assert self._transport.port is not None
        assert self._transport.authenticator is not None  # enforced by ServerTransportConfig

        app = self._server.streamable_http_app(
            host=self._transport.host,
            auth=AuthSettings(
                issuer_url=AnyHttpUrl(f"http://{self._transport.host}:{self._transport.port}"),
                resource_server_url=None,
            ),
            token_verifier=_TokenVerifierAdapter(self._transport.authenticator),
        )
        config = uvicorn.Config(
            app, host=self._transport.host, port=self._transport.port, log_level="warning"
        )
        self._uvicorn_server = uvicorn.Server(config)
        await self._uvicorn_server.serve()

    async def stop(self) -> None:
        """Signals start() to return. For stdio: closing the underlying
        streams is stdio_server()'s own responsibility, exited when
        start()'s `async with` block returns. For http: sets uvicorn's own
        should_exit flag, which start()'s serve() call is awaiting on.
        """
        if self._transport.kind == "http":
            if self._uvicorn_server is not None:
                self._uvicorn_server.should_exit = True
        else:
            self._stop_event.set()


# What this module deliberately does not cover:
#
# - SSE transport specifically (the OLDER, now-legacy `mcp` transport,
#   distinct from the modern "streamable HTTP" transport implemented
#   here) -- streamable HTTP is the currently-recommended transport in
#   the real `mcp` SDK; the legacy SSE transport was never built, since
#   building the deprecated one first would be backwards.
# - WeakIsolationError's real tier check -- SandboxPool has no queryable
#   tier attribute yet (contracts/sandbox.md never specified one); until
#   it does, allow_weak_isolation_for_external_callers is accepted but
#   inert. See Open items in contracts/mcp-server.md.
