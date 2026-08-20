"""FabricaMCPServer -- see docs/contracts/mcp-server.md.

Connects a REAL mcp.ClientSession, over a real stdio subprocess boundary,
to tests/mcp/fixtures/fabrica_stdio_server.py -- a real CivitasBridge-built
Fabrica wrapped in a real FabricaMCPServer. Not mocked anywhere in this
path: real Retriever, real SubprocessSandbox, real code-mode execution,
real in-memory long-term memory, all reached through the MCP protocol.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from typing import Any

import httpx2
import mcp.types as types
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from fabrica.civitas_bridge import CivitasBridge, Fabrica
from fabrica.mcp.server import FabricaMCPServer, ServerTransportConfig, _to_content

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
    def test_http_transport_without_authenticator_raises_at_construction(self) -> None:
        with pytest.raises(ValueError, match="requires host, port, and authenticator"):
            ServerTransportConfig(kind="http", host="localhost", port=8080)

    def test_http_transport_without_host_or_port_raises_at_construction(self) -> None:
        class _Auth:
            async def authenticate(self, token: str) -> str | None:
                return "agent-x"

        with pytest.raises(ValueError, match="requires host, port, and authenticator"):
            ServerTransportConfig(kind="http", authenticator=_Auth())

    async def test_weak_isolation_raises_by_default_against_a_real_tier_0_fabrica(self) -> None:
        # Forced to Tier 0 explicitly via the real SubprocessSandbox --
        # CivitasBridge.build() now does REAL platform dispatch
        # (select_sandbox_backend(), isolation.md), so what it produces
        # depends on the host running this test; this asserts the check
        # against a real, known Tier 0 backend deterministically, not
        # against "whatever this machine happens to dispatch to."
        from fabrica.mcp.server import WeakIsolationError
        from fabrica.sandbox import SubprocessSandbox

        fabrica = await CivitasBridge(
            allow_ungoverned=True, sandbox_backend=SubprocessSandbox()
        ).build()
        assert fabrica.tools.tier == 0
        with pytest.raises(WeakIsolationError, match="Tier 0"):
            FabricaMCPServer(fabrica, ServerTransportConfig(kind="stdio"))

    async def test_weak_isolation_does_not_raise_against_a_real_tier_2_fabrica(self) -> None:
        """The real proof this check was built for: now that
        FirecrackerSandbox (Tier 2) is real, a genuinely Tier-2-backed
        Fabrica cleanly constructs an external-facing FabricaMCPServer WITHOUT
        needing allow_weak_isolation_for_external_callers=True at all.
        Uses sandbox_backend's override to get a deterministic Tier 2
        pool on any host (a real FirecrackerSandbox object, just never
        booted here) -- the real end-to-end proof that dispatch itself
        picks FirecrackerSandbox on real Linux+KVM+Firecracker lives on
        the homelab (test_pool_with_firecracker.py, HANDOFF.md).
        """
        from fabrica.sandbox import FirecrackerSandbox

        real_but_unbooted_backend = FirecrackerSandbox(
            firecracker_binary="/usr/bin/firecracker",
            kernel_image_path="/nonexistent/kernel",
            base_rootfs_path="/nonexistent/rootfs.ext4",
        )
        fabrica = await CivitasBridge(
            allow_ungoverned=True, sandbox_backend=real_but_unbooted_backend
        ).build()
        assert fabrica.tools.tier == 2

        server = FabricaMCPServer(fabrica, ServerTransportConfig(kind="stdio"))
        assert server is not None

    async def test_weak_isolation_allowed_with_explicit_opt_in(self) -> None:
        from fabrica.sandbox import SubprocessSandbox

        # Pinned explicitly -- this test's whole point is proving the
        # opt-in bypasses the check regardless of tier, not exercising
        # real dispatch (which would otherwise construct, and leak absent
        # a close(), whatever backend this host's real dispatch picks).
        fabrica = await CivitasBridge(
            allow_ungoverned=True, sandbox_backend=SubprocessSandbox()
        ).build()
        # Must not raise.
        FabricaMCPServer(
            fabrica,
            ServerTransportConfig(kind="stdio"),
            allow_weak_isolation_for_external_callers=True,
        )


def test_to_content_serializes_dataclass_lists_as_json() -> None:
    from fabrica.retriever.types import Indexable, RankedMatch

    item = Indexable(id="x", kind="tool", name="x", description="d")
    content = _to_content([RankedMatch(item=item, rank=0)])
    assert isinstance(content[0], types.TextContent)
    payload = json.loads(content[0].text)
    expected_item = {"id": "x", "kind": "tool", "name": "x", "description": "d", "eager": False}
    assert payload == [{"item": expected_item, "rank": 0}]


# ---------------------------------------------------------------------------
# HTTP transport -- a REAL uvicorn server + a REAL mcp.client.streamable_http
# client, real bearer-token accept/reject via the mcp library's own auth
# middleware. Not mocked anywhere in this path.
# ---------------------------------------------------------------------------

_HTTP_HOST = "127.0.0.1"
_HTTP_PORT = 8931
_HTTP_URL = f"http://{_HTTP_HOST}:{_HTTP_PORT}/mcp"
_GOOD_TOKEN = "good-token"  # noqa: S105 -- test fixture value, not a real credential


class _FixedTokenAuthenticator:
    """Real TokenAuthenticator implementation -- resolves exactly one
    known token to exactly one agent_id, denies everything else.
    """

    async def authenticate(self, token: str) -> str | None:
        return "http-agent-1" if token == _GOOD_TOKEN else None


class _RunningHttpServer:
    """Starts a real FabricaMCPServer over HTTP in a background task for
    the duration of the `async with` block, and stops it cleanly after.
    """

    def __init__(self) -> None:
        self._server: FabricaMCPServer | None = None
        self._task: asyncio.Task[None] | None = None
        self._fabrica: Fabrica | None = None

    async def __aenter__(self) -> FabricaMCPServer:
        from fabrica.sandbox import SubprocessSandbox

        # Pinned explicitly, not real dispatch -- what's under test here
        # is the HTTP transport/auth flow, not sandbox tier; real dispatch
        # would otherwise construct (and leak, absent a fabrica.close()
        # this fixture didn't used to call at all) whatever backend this
        # host picks, e.g. a real SrtSandbox.
        fabrica = await CivitasBridge(
            allow_ungoverned=True, sandbox_backend=SubprocessSandbox()
        ).build()
        self._fabrica = fabrica
        transport = ServerTransportConfig(
            kind="http", host=_HTTP_HOST, port=_HTTP_PORT, authenticator=_FixedTokenAuthenticator()
        )
        # allow_weak_isolation_for_external_callers=True: honest, not a
        # workaround -- this fixture genuinely IS exposing a Tier 0
        # backend to an external caller over real HTTP.
        self._server = FabricaMCPServer(
            fabrica, transport, allow_weak_isolation_for_external_callers=True
        )
        self._task = asyncio.ensure_future(self._server.start())
        await asyncio.sleep(0.4)  # real socket bind -- give uvicorn time to start listening
        return self._server

    async def __aexit__(self, *exc: object) -> None:
        assert self._server is not None and self._task is not None and self._fabrica is not None
        await self._server.stop()
        await self._task
        await self._fabrica.close()


def _http_client(token: str | None) -> httpx2.AsyncClient:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx2.AsyncClient(headers=headers)


class TestHttpTransport:
    async def test_authenticated_request_lists_the_five_fixed_tools(self) -> None:
        async with _RunningHttpServer():
            client = _http_client(_GOOD_TOKEN)
            async with (
                streamable_http_client(_HTTP_URL, http_client=client) as (read, write),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                tools = await session.list_tools()
        names = {t.name for t in tools.tools}
        assert names == {
            "fabrica_find",
            "fabrica_run_code",
            "fabrica_run_skill",
            "fabrica_memory_write",
            "fabrica_memory_search",
        }

    async def test_unauthenticated_request_is_rejected(self) -> None:
        async with _RunningHttpServer():
            client = _http_client(None)
            with pytest.raises(Exception):  # noqa: B017, PT011 -- real transport-level rejection
                async with (
                    streamable_http_client(_HTTP_URL, http_client=client) as (read, write),
                    ClientSession(read, write) as session,
                ):
                    await session.initialize()

    async def test_wrong_token_is_rejected(self) -> None:
        async with _RunningHttpServer():
            client = _http_client("wrong-token")
            with pytest.raises(Exception):  # noqa: B017, PT011 -- real transport-level rejection
                async with (
                    streamable_http_client(_HTTP_URL, http_client=client) as (read, write),
                    ClientSession(read, write) as session,
                ):
                    await session.initialize()

    async def test_run_code_resolves_agent_id_from_the_verified_token(self) -> None:
        # fabrica_run_code's agent_id is resolved from the connection's
        # verified token (via _resolve_agent_id/get_access_token), never
        # from caller-supplied arguments -- proven by checking the actual
        # Scope a real code-mode execution used, not just that the call
        # succeeded.
        async with _RunningHttpServer():
            client = _http_client(_GOOD_TOKEN)
            async with (
                streamable_http_client(_HTTP_URL, http_client=client) as (read, write),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                write_result = await session.call_tool(
                    "fabrica_memory_write", {"content": "written over real HTTP"}
                )
                assert write_result.is_error is not True
                search_result = await session.call_tool(
                    "fabrica_memory_search", {"query": "written over real HTTP"}
                )
        items = _text_payload(search_result)
        assert len(items) == 1  # only visible under http-agent-1's own Scope
